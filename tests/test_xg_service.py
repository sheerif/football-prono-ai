import math
import hashlib
import unittest
from unittest.mock import Mock, patch

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DatabaseError

from database import models
from services import full_sync_service, import_service, prediction_helpers, xg_service
from services.api_football import ApiFootballClient


class XgServiceTests(unittest.TestCase):
    def _engine(self):
        test_engine = create_engine("sqlite:///:memory:")
        with test_engine.begin() as conn:
            conn.execute(text("CREATE TABLE teams (id INTEGER PRIMARY KEY, name TEXT)"))
            conn.execute(
                text(
                    """
                    CREATE TABLE matches (
                        fixture_id INTEGER PRIMARY KEY,
                        league_id INTEGER NOT NULL,
                        season INTEGER NOT NULL,
                        date TEXT NOT NULL,
                        home_team_id INTEGER NOT NULL,
                        away_team_id INTEGER NOT NULL,
                        home_goals INTEGER,
                        away_goals INTEGER,
                        winner TEXT,
                        status TEXT
                    )
                    """
                )
            )
            conn.execute(
                text(
                    """
                    CREATE TABLE xg_ingestion_audit (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        sync_run_id TEXT NOT NULL,
                        fixture_id INTEGER NOT NULL,
                        source TEXT NOT NULL,
                        endpoint TEXT NOT NULL,
                        request_json TEXT NOT NULL,
                        status TEXT NOT NULL,
                        requested_at TEXT NOT NULL,
                        completed_at TEXT NOT NULL,
                        item_count INTEGER NOT NULL DEFAULT 0,
                        has_xg INTEGER NOT NULL DEFAULT 0,
                        payload_sha256 TEXT,
                        response_json TEXT,
                        error TEXT
                    )
                    """
                )
            )
            conn.execute(
                text(
                    """
                    CREATE TABLE fixture_team_statistics (
                        fixture_id INTEGER NOT NULL,
                        team_id INTEGER NOT NULL,
                        team_name TEXT,
                        is_home INTEGER,
                        expected_goals REAL,
                        goals_prevented REAL,
                        source TEXT NOT NULL DEFAULT 'API-Football',
                        source_endpoint TEXT NOT NULL DEFAULT '/fixtures/statistics',
                        source_field TEXT NOT NULL DEFAULT 'expected_goals',
                        retrieved_at TEXT,
                        payload_sha256 TEXT,
                        ingestion_id INTEGER,
                        raw_json TEXT,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (fixture_id, team_id)
                    )
                    """
                )
            )
            conn.execute(text("INSERT INTO teams VALUES (10, 'Home'), (20, 'Away')"))
        return test_engine

    def test_model_and_index_are_declared(self):
        self.assertEqual(models.FixtureTeamStatistic.__tablename__, "fixture_team_statistics")
        self.assertIn(
            "ix_fixture_team_statistics_team",
            {index.name for index in models.FixtureTeamStatistic.__table__.indexes},
        )
        self.assertEqual(models.XgIngestionAudit.__tablename__, "xg_ingestion_audit")

    def test_api_client_uses_fixture_statistics_endpoint(self):
        client = ApiFootballClient(api_key="test")
        with patch.object(client, "_get", return_value={"response": []}) as get:
            client.get_fixture_statistics(123)
        get.assert_called_once_with("/fixtures/statistics", {"fixture": 123})

    def test_parser_accepts_xg_and_signed_goals_prevented(self):
        rows = xg_service.parse_fixture_statistics(
            [
                {
                    "team": {"id": 10, "name": "Home"},
                    "statistics": [
                        {"type": "expected_goals", "value": "1.66"},
                        {"type": "goals_prevented", "value": "-0.30"},
                    ],
                }
            ]
        )
        self.assertEqual(rows[0]["expected_goals"], 1.66)
        self.assertEqual(rows[0]["goals_prevented"], -0.30)
        self.assertIsNone(xg_service._number(float("nan")))
        self.assertIsNone(xg_service._number(-0.1, nonnegative=True))

    def test_save_persists_both_sides_and_updates_existing_values(self):
        test_engine = self._engine()
        with test_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO matches VALUES "
                    "(1, 61, 2026, '2026-01-01', 10, 20, 2, 1, 'Home', 'Match Finished')"
                )
            )
        payload = [
            {"team": {"id": 10, "name": "Home"}, "statistics": [{"type": "expected_goals", "value": "1.4"}]},
            {"team": {"id": 20, "name": "Away"}, "statistics": [{"type": "expected_goals", "value": "0.7"}]},
        ]
        with patch.object(xg_service, "engine", test_engine):
            self.assertEqual(xg_service.save_fixture_statistics(1, payload), 2)
            payload[0]["statistics"][0]["value"] = "1.6"
            xg_service.save_fixture_statistics(1, payload)
            with test_engine.connect() as conn:
                rows = conn.execute(
                    text(
                        "SELECT team_id, is_home, expected_goals "
                        "FROM fixture_team_statistics ORDER BY team_id"
                    )
                ).fetchall()
                audits = conn.execute(
                    text(
                        "SELECT sync_run_id, status, has_xg, payload_sha256, response_json "
                        "FROM xg_ingestion_audit ORDER BY id"
                    )
                ).fetchall()
        self.assertEqual(rows, [(10, 1, 1.6), (20, 0, 0.7)])
        self.assertEqual(len(audits), 2)
        self.assertTrue(all(row[0] for row in audits))
        self.assertTrue(all(row[1] == "available" for row in audits))
        self.assertTrue(all(row[2] == 1 for row in audits))
        self.assertTrue(all(len(row[3]) == 64 for row in audits))
        self.assertTrue(all("expected_goals" in row[4] for row in audits))
        for row in audits:
            self.assertEqual(
                row[3], hashlib.sha256(row[4].encode("utf-8")).hexdigest()
            )

    def test_unavailable_and_error_attempts_are_append_only(self):
        test_engine = self._engine()
        with test_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO matches VALUES "
                    "(1, 61, 2026, '2026-01-01', 10, 20, 2, 1, 'Home', 'Match Finished')"
                )
            )
        with patch.object(xg_service, "engine", test_engine):
            first = xg_service.record_ingestion(
                1,
                sync_run_id="lot-1",
                status="unavailable",
                requested_at="2026-01-02T10:00:00",
                payload={"response": []},
            )
            second = xg_service.record_ingestion(
                1,
                sync_run_id="lot-2",
                status="error",
                requested_at="2026-01-03T10:00:00",
                error="429 quota reached",
            )
            with test_engine.connect() as conn:
                rows = conn.execute(
                    text("SELECT id, status, error FROM xg_ingestion_audit ORDER BY id")
                ).fetchall()
        self.assertNotEqual(first, second)
        self.assertEqual(rows, [(first, "unavailable", None), (second, "error", "429 quota reached")])

    def test_database_triggers_make_audit_immutable(self):
        test_engine = self._engine()
        with test_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO matches VALUES "
                    "(1, 61, 2026, '2026-01-01', 10, 20, 2, 1, 'Home', 'Match Finished')"
                )
            )
        with (
            patch.object(import_service, "engine", test_engine),
            patch.object(xg_service, "engine", test_engine),
        ):
            import_service._ensure_fixture_api_cache_tables()
            audit_id = xg_service.record_ingestion(
                1,
                sync_run_id="immutable-lot",
                status="unavailable",
                requested_at="2026-01-02T10:00:00",
                payload={"response": []},
            )
            with self.assertRaises(DatabaseError):
                with test_engine.begin() as conn:
                    conn.execute(
                        text("UPDATE xg_ingestion_audit SET status = 'available' WHERE id = :id"),
                        {"id": audit_id},
                    )
            with self.assertRaises(DatabaseError):
                with test_engine.begin() as conn:
                    conn.execute(
                        text("DELETE FROM xg_ingestion_audit WHERE id = :id"),
                        {"id": audit_id},
                    )

    def test_historical_loader_excludes_xg_at_or_after_kickoff(self):
        test_engine = self._engine()
        with test_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO matches VALUES "
                    "(1, 61, 2026, '2026-01-01T12:00:00', 10, 20, 2, 1, 'Home', 'Match Finished'),"
                    "(2, 61, 2026, '2026-02-01T12:00:00', 10, 20, 1, 0, 'Home', 'Match Finished')"
                )
            )
            for fixture_id, home_xg, away_xg in ((1, 1.5, 0.8), (2, 9.9, 9.8)):
                conn.execute(
                    text(
                        "INSERT INTO fixture_team_statistics "
                        "(fixture_id, team_id, team_name, is_home, expected_goals, "
                        "goals_prevented, raw_json, updated_at) VALUES "
                        "(:fixture, 10, 'Home', 1, :home_xg, NULL, '{}', '2026-03-01'),"
                        "(:fixture, 20, 'Away', 0, :away_xg, NULL, '{}', '2026-03-01')"
                    ),
                    {"fixture": fixture_id, "home_xg": home_xg, "away_xg": away_xg},
                )
        with patch.object(prediction_helpers, "engine", test_engine):
            history = prediction_helpers.load_historical_context(
                61, "2026-02-01T12:00:00"
            )
        self.assertEqual(history["fixture_id"].tolist(), [1])
        self.assertEqual(history.iloc[0]["home_xg"], 1.5)

    def test_summary_reports_xg_for_against_and_coverage(self):
        matches = pd.DataFrame(
            [
                {"date": "2026-01-03", "home_team_id": 10, "away_team_id": 20, "home_xg": 2.0, "away_xg": 1.0},
                {"date": "2026-01-02", "home_team_id": 20, "away_team_id": 10, "home_xg": 0.5, "away_xg": 1.5},
                {"date": "2026-01-01", "home_team_id": 10, "away_team_id": 20, "home_xg": math.nan, "away_xg": math.nan},
            ]
        )
        summary = xg_service.summarize_team(matches, 10, limit=3)
        self.assertEqual(summary["matches"], 2)
        self.assertEqual(summary["coverage"], 0.667)
        self.assertEqual(summary["xg_for"], 1.75)
        self.assertEqual(summary["xg_against"], 0.75)
        self.assertEqual(summary["difference"], 1.0)

    def test_xg_sync_stops_on_quota_and_preserves_previous_progress(self):
        rows = [{"fixture_id": 1}, {"fixture_id": 2}]
        client = Mock()
        client.get_fixture_statistics.side_effect = [
            {"response": [{"team": {"id": 10}, "statistics": [{"type": "expected_goals", "value": "1.2"}]}]},
            RuntimeError("429 quota reached"),
        ]
        with (
            patch.object(full_sync_service, "client", client),
            patch.object(xg_service, "fixture_statistics_present", return_value=False),
            patch.object(xg_service, "save_fixture_statistics", return_value=1),
            patch.object(xg_service, "record_ingestion"),
            patch.object(full_sync_service.sync_registry, "get", return_value=None),
            patch.object(full_sync_service.sync_registry, "mark"),
            patch.object(full_sync_service.time, "sleep"),
        ):
            result = full_sync_service._sync_xg_rows(
                rows, pause=0, retry_hours=24
            )
        self.assertEqual(result["downloaded"], 1)
        self.assertTrue(result["quota_reached"])


if __name__ == "__main__":
    unittest.main()
