import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, text

from pages import analyse_match


class AnalysisXgTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE matches (
                        fixture_id INTEGER PRIMARY KEY, league_id INTEGER,
                        season INTEGER, date TEXT, home_team_id INTEGER,
                        away_team_id INTEGER, home_goals INTEGER,
                        away_goals INTEGER, winner TEXT, status TEXT
                    )
                    """
                )
            )
            conn.execute(
                text(
                    """
                    CREATE TABLE fixture_team_statistics (
                        fixture_id INTEGER, team_id INTEGER,
                        expected_goals REAL, goals_prevented REAL,
                        retrieved_at TEXT, payload_sha256 TEXT,
                        PRIMARY KEY (fixture_id, team_id)
                    )
                    """
                )
            )
            conn.execute(
                text(
                    """
                    INSERT INTO matches VALUES
                    (42, 61, 2026, '2026-09-01T18:00:00', 1, 2, 2, 1, 'home', 'Match Finished')
                    """
                )
            )
            conn.execute(
                text(
                    """
                    INSERT INTO fixture_team_statistics
                    (fixture_id, team_id, expected_goals, goals_prevented, retrieved_at, payload_sha256)
                    VALUES
                    (42, 1, 1.85, 0.2, '2026-09-02T00:00:00', 'home-hash'),
                    (42, 2, 0.72, -0.1, '2026-09-02T00:00:00', 'away-hash')
                    """
                )
            )

    def tearDown(self):
        self.engine.dispose()

    def test_analysis_loader_joins_both_xg_sides(self):
        with patch.object(analyse_match, "engine", self.engine):
            matches = analyse_match._load_matches_window(61, [2026])

        self.assertEqual(len(matches), 1)
        self.assertEqual(float(matches.iloc[0]["home_xg"]), 1.85)
        self.assertEqual(float(matches.iloc[0]["away_xg"]), 0.72)
        self.assertEqual(matches.iloc[0]["home_xg_payload_sha256"], "home-hash")

    def test_xg_history_is_presented_from_team_viewpoint(self):
        with patch.object(analyse_match, "engine", self.engine):
            matches = analyse_match._load_matches_window(61, [2026])

        history = analyse_match._team_xg_history_table(
            matches, 2, {1: "Paris", 2: "Marseille"}
        )

        self.assertEqual(len(history), 1)
        self.assertEqual(history.iloc[0]["Adversaire"], "Paris")
        self.assertEqual(float(history.iloc[0]["xG"]), 0.72)
        self.assertEqual(float(history.iloc[0]["xGA"]), 1.85)
        self.assertEqual(float(history.iloc[0]["Différentiel"]), -1.13)


if __name__ == "__main__":
    unittest.main()
