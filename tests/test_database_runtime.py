import unittest
import datetime
import json
import sys
from unittest.mock import Mock, patch

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from database import models
from database import database as database_runtime
from database.database import engine
from services import background_jobs, import_service


class DatabaseRuntimeTests(unittest.TestCase):
    def test_adapter_import_retries_after_streamlit_hot_reload_keyerror(self):
        adapter = object()
        module_name = "database.temporary_adapter"
        sys.modules[module_name] = Mock()
        try:
            with patch.object(
                database_runtime.importlib,
                "import_module",
                side_effect=[KeyError(module_name), adapter],
            ) as importer:
                loaded = database_runtime._load_database_adapter(
                    "temporary_adapter"
                )
        finally:
            sys.modules.pop(module_name, None)

        self.assertIs(loaded, adapter)
        self.assertEqual(importer.call_count, 2)

    def test_repeated_team_in_same_batch_is_inserted_once(self):
        test_engine = create_engine("sqlite://")
        models.Base.metadata.create_all(test_engine)
        session = sessionmaker(bind=test_engine)()
        try:
            first = import_service._get_or_create_team(
                session,
                {"id": 759, "name": "Viking", "logo": "first.png"},
                league_id=2,
            )
            second = import_service._get_or_create_team(
                session,
                {"id": 759, "name": "Viking", "logo": "latest.png"},
                league_id=2,
            )
            session.commit()

            self.assertIs(first, second)
            self.assertEqual(session.query(models.Team).count(), 1)
            self.assertEqual(session.get(models.Team, 759).logo, "latest.png")
        finally:
            session.close()
            test_engine.dispose()

    def test_current_remote_schema_skips_slow_ddl_checks(self):
        previous = import_service._db_initialized
        import_service._db_initialized = False
        try:
            with (
                patch.object(import_service, "persistence_mode", return_value="turso"),
                patch.object(import_service, "_remote_schema_is_current", return_value=True),
                patch.object(models.Base.metadata, "create_all") as create_all,
            ):
                import_service.init_db()
                import_service.init_db()
        finally:
            import_service._db_initialized = previous

        create_all.assert_not_called()

    def test_complete_historical_season_sends_no_api_request(self):
        session = Mock()
        session.query.return_value.filter_by.return_value.count.return_value = 10
        with (
            patch.object(import_service, "SessionLocal", return_value=session),
            patch.object(import_service, "register_league_seasons"),
            patch.object(import_service, "_sync_league_metadata") as metadata,
            patch.object(import_service.client, "get_teams") as teams,
            patch.object(import_service.client, "get_fixtures") as fixtures,
            patch.object(import_service.client, "get_standings") as standings,
        ):
            import_service.import_leagues_cautious(
                [61], seasons=[2025], pause=0, max_retries=1
            )

        metadata.assert_not_called()
        teams.assert_not_called()
        fixtures.assert_not_called()
        standings.assert_not_called()

    def test_missing_standings_do_not_redownload_teams_or_fixtures(self):
        session = Mock()
        counts = iter([10, 20, 0, 10])
        session.query.return_value.filter_by.return_value.count.side_effect = (
            lambda: next(counts)
        )
        league = Mock(name="Ligue 1", logo="logo.png")
        session.get.return_value = league
        with (
            patch.object(import_service, "SessionLocal", return_value=session),
            patch.object(import_service, "register_league_seasons"),
            patch.object(import_service, "_sync_league_metadata") as metadata,
            patch.object(import_service.client, "get_teams") as teams,
            patch.object(import_service.client, "get_fixtures") as fixtures,
            patch.object(
                import_service.client,
                "get_standings",
                return_value={"response": []},
            ) as standings,
            patch.object(import_service.time, "sleep"),
        ):
            import_service.import_leagues_cautious(
                [61], seasons=[2025], pause=0, max_retries=1
            )

        metadata.assert_not_called()
        teams.assert_not_called()
        fixtures.assert_not_called()
        standings.assert_called_once_with(61, 2025)

    def test_fixture_response_is_reused_for_detail_storage(self):
        test_engine = create_engine("sqlite://")
        item = {
            "fixture": {
                "id": 42,
                "venue": {"name": "Stade Test", "city": "Paris"},
                "status": {"short": "NS"},
            },
            "league": {
                "id": 61,
                "season": 2026,
                "round": "Regular Season - 1",
                "logo": "league.png",
            },
            "teams": {
                "home": {"logo": "home.png"},
                "away": {"logo": "away.png"},
            },
        }
        with test_engine.begin() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE fixture_api_details (
                        fixture_id INTEGER PRIMARY KEY, league_id INTEGER,
                        season INTEGER, round TEXT, venue TEXT, city TEXT,
                        status_short TEXT, home_logo TEXT, away_logo TEXT,
                        league_logo TEXT, raw_json TEXT, updated_at TEXT
                    )
                    """
                )
            )
            import_service._save_fixture_api_detail(conn, item, 61, 2026)
            row = conn.execute(
                text(
                    "SELECT round, venue, home_logo, away_logo "
                    "FROM fixture_api_details WHERE fixture_id = 42"
                )
            ).one()

        self.assertEqual(
            tuple(row),
            ("Regular Season - 1", "Stade Test", "home.png", "away.png"),
        )

    def test_season_access_audit_reuses_same_persistent_scope(self):
        config = {
            "league_ids": [61],
            "start_season": 2025,
            "end_season": 2026,
        }
        signature = json.dumps(
            {"sample_league": 61, "seasons": [2025, 2026]},
            sort_keys=True,
            separators=(",", ":"),
        )
        stored = json.dumps(
            {"sample_league": 61, "accessible": [2025, 2026], "unavailable": []}
        )
        with (
            patch.object(
                import_service,
                "_get_sync_value",
                side_effect=lambda key: {
                    "last_api_access_signature": signature,
                    "last_api_access_audit": stored,
                }.get(key),
            ),
            patch.object(import_service.client, "get_fixtures") as get_fixtures,
        ):
            result = import_service.audit_configured_season_access(config)

        get_fixtures.assert_not_called()
        self.assertTrue(result["cached"])

    def test_current_competitions_skip_calls_inside_refresh_interval(self):
        config = {
            "enabled": True,
            "current_enabled": True,
            "interval_minutes": 360,
        }
        recent = (
            datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
            - datetime.timedelta(minutes=10)
        ).isoformat()
        with (
            patch.object(import_service, "get_auto_refresh_config", return_value=config),
            patch.object(import_service, "_get_sync_value", return_value=recent),
            patch.object(import_service, "SessionLocal") as session_factory,
        ):
            result = import_service.refresh_current_competitions_on_connection()

        session_factory.assert_not_called()
        self.assertFalse(result["ran"])
        self.assertTrue(result["cached"])

    def test_core_import_does_not_retry_a_quota_error(self):
        session = Mock()
        session.query.return_value.filter_by.return_value.count.return_value = 0
        quota_error = RuntimeError("API-Football quota 429: daily limit")
        with (
            patch.object(import_service, "SessionLocal", return_value=session),
            patch.object(import_service, "register_league_seasons"),
            patch.object(import_service, "_sync_league_metadata"),
            patch.object(import_service.client, "get_teams", side_effect=quota_error) as get_teams,
            patch.object(import_service.time, "sleep"),
        ):
            with self.assertRaisesRegex(RuntimeError, "quota 429"):
                import_service.import_leagues_cautious(
                    [61], seasons=[2026], pause=0, max_retries=3
                )

        get_teams.assert_called_once()

    def test_update_log_deduplicates_same_event_within_one_minute(self):
        from sqlalchemy import create_engine, text

        test_engine = create_engine("sqlite://")
        with patch.object(import_service, "engine", test_engine):
            first = import_service.record_update_log(
                "synchronisation_globale",
                "en_attente_quota",
                finished_at="2026-09-07T20:47:00",
                reason="Quota journalier atteint",
            )
            second = import_service.record_update_log(
                "synchronisation_globale",
                "en_attente_quota",
                finished_at="2026-09-07T20:47:20",
                reason="Quota journalier atteint",
            )
            with test_engine.connect() as conn:
                count = conn.execute(text("SELECT COUNT(*) FROM update_log")).scalar()

        self.assertEqual(first, second)
        self.assertEqual(count, 1)

    def test_update_log_message_explains_rows_without_comment(self):
        success = import_service.update_log_message(
            "championnats_en_cours", "effectuée"
        )
        ignored = import_service.update_log_message(
            "championnats_en_cours", "ignorée"
        )
        detailed = import_service.update_log_message(
            "historique_auto",
            "ignorée",
            details='{"reason":"Synchronisation récente, aucun appel API relancé."}',
        )
        pandas_null_error = import_service.update_log_message(
            "championnats_en_cours",
            "effectuée",
            reason="Championnats en cours mis à jour.",
            error=float("nan"),
        )

        self.assertIn("succès", success)
        self.assertIn("déjà à jour", ignored)
        self.assertEqual(
            detailed, "Synchronisation récente, aucun appel API relancé."
        )
        self.assertEqual(
            pandas_null_error, "Championnats en cours mis à jour."
        )

    def test_sqlite_connections_enable_integrity_and_lock_protection(self):
        if engine.dialect.name != "sqlite":
            self.skipTest("SQLite-specific runtime settings")
        with engine.connect() as connection:
            foreign_keys = connection.exec_driver_sql(
                "PRAGMA foreign_keys"
            ).scalar_one()
            busy_timeout = connection.exec_driver_sql(
                "PRAGMA busy_timeout"
            ).scalar_one()
            journal_mode = connection.exec_driver_sql(
                "PRAGMA journal_mode"
            ).scalar_one()
        self.assertEqual(foreign_keys, 1)
        self.assertGreaterEqual(busy_timeout, 30_000)
        self.assertEqual(str(journal_mode).casefold(), "wal")

    def test_performance_indexes_are_declared_in_metadata(self):
        declared = {
            index.name
            for table in models.Base.metadata.sorted_tables
            for index in table.indexes
        }
        self.assertTrue(
            {
                "ix_teams_league_id",
                "ix_matches_league_season_date",
                "ix_matches_home_team_date",
                "ix_matches_away_team_date",
                "ix_matches_date_scores",
                "ix_player_statistics_league_season_team",
                "ix_fixture_team_statistics_team",
                "ix_xg_ingestion_audit_fixture",
                "ix_xg_ingestion_audit_run",
            }.issubset(declared)
        )

    def test_only_one_data_job_can_be_registered_at_a_time(self):
        with patch.object(background_jobs, "_jobs", {}):
            first_id, first_created = background_jobs._create_unique_data_job(
                "manual_import",
                "Premier import",
            )
            second_id, second_created = background_jobs._create_unique_data_job(
                "full_sync",
                "Deuxième import",
            )
            self.assertTrue(first_created)
            self.assertFalse(second_created)
            self.assertEqual(first_id, second_id)
            self.assertTrue(background_jobs.data_job_running())

    def test_import_helpers_leave_transaction_control_to_the_batch(self):
        session = Mock()
        session.get.return_value = None

        import_service._get_or_create_team(
            session,
            {"id": 85, "name": "Test FC"},
            league_id=61,
        )
        import_service._save_match(
            session,
            {
                "fixture": {
                    "id": 12345,
                    "date": "2026-08-01T18:00:00Z",
                    "status": {"long": "Not Started"},
                },
                "teams": {
                    "home": {"id": 85},
                    "away": {"id": 86},
                },
                "goals": {"home": None, "away": None},
            },
            league_id=61,
            season=2026,
        )

        self.assertEqual(session.add.call_count, 2)
        session.commit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
