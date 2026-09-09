import datetime
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import pandas as pd
from sqlalchemy import create_engine, text

from pages import matchs_a_venir


class MatchdayDisplayTests(unittest.TestCase):
    def test_cached_previews_do_not_requery_remote_dependencies(self):
        upcoming = pd.DataFrame(
            [
                {
                    "fixture_id": 123,
                    "league_id": 61,
                    "season": 2026,
                    "date": "2026-09-12T18:00:00Z",
                    "home_team_id": 10,
                    "away_team_id": 20,
                    "home_name": "Nice",
                    "away_name": "Paris Saint Germain",
                }
            ]
        )
        cached = {
            123: {
                "fixture_id": 123,
                "source_hash": "already-calculated",
                "match_label": "Nice - Paris Saint Germain",
            }
        }

        with (
            patch.object(matchs_a_venir.schema_guard, "ensure_fixture_api_cache_tables"),
            patch.object(matchs_a_venir, "_load_cached_previews", return_value=cached),
            patch.object(matchs_a_venir, "_load_prediction_context") as load_context,
            patch.object(matchs_a_venir, "_preview_source_hash") as source_hash,
            patch.object(matchs_a_venir, "_build_match_preview") as build_preview,
            patch.object(matchs_a_venir, "_save_match_preview") as save_preview,
        ):
            result = matchs_a_venir._build_previews(upcoming, 10)

        self.assertEqual(result.iloc[0]["Match"], "Nice - Paris Saint Germain")
        load_context.assert_not_called()
        source_hash.assert_not_called()
        build_preview.assert_not_called()
        save_preview.assert_not_called()

    def test_explicit_refresh_still_validates_cached_preview(self):
        upcoming = pd.DataFrame(
            [
                {
                    "fixture_id": 123,
                    "league_id": 61,
                    "season": 2026,
                    "date": "2026-09-12T18:00:00Z",
                    "home_team_id": 10,
                    "away_team_id": 20,
                    "home_name": "Nice",
                    "away_name": "Paris Saint Germain",
                }
            ]
        )
        cached = {
            123: {
                "fixture_id": 123,
                "source_hash": "current-hash",
                "match_label": "Nice - Paris Saint Germain",
            }
        }

        with (
            patch.object(matchs_a_venir.schema_guard, "ensure_fixture_api_cache_tables"),
            patch.object(matchs_a_venir, "_load_cached_previews", return_value=cached),
            patch.object(
                matchs_a_venir,
                "_load_prediction_context",
                return_value=pd.DataFrame(),
            ) as load_context,
            patch.object(
                matchs_a_venir,
                "_preview_source_hash",
                return_value="current-hash",
            ) as source_hash,
            patch.object(matchs_a_venir, "_build_match_preview") as build_preview,
            patch.object(matchs_a_venir, "_save_match_preview") as save_preview,
        ):
            matchs_a_venir._build_previews(upcoming, 10, refresh_stale=True)

        load_context.assert_called_once()
        source_hash.assert_called_once()
        build_preview.assert_not_called()
        save_preview.assert_not_called()

    def _schedule_engine(self):
        engine = create_engine("sqlite://")
        now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE leagues (id INTEGER, name TEXT, country TEXT)"))
            conn.execute(text("CREATE TABLE teams (id INTEGER, name TEXT)"))
            conn.execute(
                text(
                    "CREATE TABLE matches (fixture_id INTEGER, league_id INTEGER, "
                    "season INTEGER, date TEXT, home_team_id INTEGER, away_team_id INTEGER, "
                    "home_goals INTEGER, away_goals INTEGER, status TEXT)"
                )
            )
            conn.execute(text("INSERT INTO leagues VALUES (61, 'Ligue 1', 'France')"))
            conn.execute(text("INSERT INTO teams VALUES (10, 'Domicile'), (20, 'Extérieur')"))
            conn.execute(
                text(
                    "INSERT INTO matches VALUES "
                    "(1, 61, 2026, :past, 10, 20, 2, 1, 'Match Finished'), "
                    "(2, 61, 2026, :future, 20, 10, NULL, NULL, 'Not Started'), "
                    "(3, 61, 2025, :older, 10, 20, 0, 0, 'Match Finished')"
                ),
                {
                    "past": (now - datetime.timedelta(days=1)).isoformat(),
                    "future": (now + datetime.timedelta(days=1)).isoformat(),
                    "older": (now - datetime.timedelta(days=300)).isoformat(),
                },
            )
        return engine

    def test_active_season_includes_played_and_upcoming_matches(self):
        with patch.object(matchs_a_venir, "engine", self._schedule_engine()):
            rows = matchs_a_venir._load_upcoming_matches(30, [61])

        self.assertEqual(set(rows["fixture_id"]), {1, 2})
        played = rows.loc[rows["fixture_id"] == 1].iloc[0]
        self.assertEqual(played["home_goals"], 2)
        self.assertEqual(played["away_goals"], 1)

    def test_played_matches_never_trigger_post_kickoff_predictions(self):
        rows = pd.DataFrame(
            [
                {
                    "fixture_id": 1,
                    "scheduled_at": "2026-09-05T18:00:00Z",
                    "home_goals": 2,
                    "away_goals": 1,
                },
                {
                    "fixture_id": 2,
                    "scheduled_at": "2026-09-07T18:00:00Z",
                    "home_goals": None,
                    "away_goals": None,
                },
            ]
        )

        fixture_ids = matchs_a_venir._future_fixture_ids(
            rows, now="2026-09-06T12:00:00Z"
        )

        self.assertEqual(fixture_ids, [2])

    def test_historical_prediction_uses_projected_not_official_lineup(self):
        match = SimpleNamespace(
            fixture_id=1,
            league_id=61,
            season=2026,
            date="2026-09-05T18:00:00Z",
            home_team_id=10,
            away_team_id=20,
            home_goals=2,
            away_goals=1,
        )
        projected = Mock(return_value={"complete": True})
        official = Mock()
        with (
            patch.object(
                matchs_a_venir.lineup_service,
                "get_projected_match_intelligence",
                projected,
            ),
            patch.object(
                matchs_a_venir.lineup_service,
                "get_match_intelligence",
                official,
            ),
        ):
            result = matchs_a_venir._prediction_player_intelligence(match)

        self.assertEqual(result, {"complete": True})
        projected.assert_called_once()
        official.assert_not_called()

    def test_observed_statistics_are_read_from_persisted_raw_payload(self):
        engine = create_engine("sqlite://")
        payloads = [
            {
                "team": {"id": 10, "name": "Domicile"},
                "statistics": [
                    {"type": "Shots on Goal", "value": 6},
                    {"type": "Ball Possession", "value": "58%"},
                ],
            },
            {
                "team": {"id": 20, "name": "Extérieur"},
                "statistics": [
                    {"type": "Shots on Goal", "value": 3},
                    {"type": "Ball Possession", "value": "42%"},
                ],
            },
        ]
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE fixture_team_statistics (fixture_id INTEGER, "
                    "team_name TEXT, is_home INTEGER, expected_goals REAL, "
                    "goals_prevented REAL, raw_json TEXT)"
                )
            )
            for is_home, payload, xg in ((1, payloads[0], 1.8), (0, payloads[1], 0.9)):
                conn.execute(
                    text(
                        "INSERT INTO fixture_team_statistics VALUES "
                        "(1, :team, :is_home, :xg, NULL, :raw_json)"
                    ),
                    {
                        "team": payload["team"]["name"],
                        "is_home": is_home,
                        "xg": xg,
                        "raw_json": json.dumps(payload),
                    },
                )

        with patch.object(matchs_a_venir, "engine", engine):
            stats = matchs_a_venir._observed_fixture_statistics(1)

        self.assertEqual(stats.iloc[0].to_dict(), {
            "Statistique": "xG",
            "Domicile": 1.8,
            "Extérieur": 0.9,
        })
        shots = stats.loc[stats["Statistique"] == "Tirs cadrés"].iloc[0]
        self.assertEqual(shots["Domicile"], 6)
        self.assertEqual(shots["Extérieur"], 3)


if __name__ == "__main__":
    unittest.main()
