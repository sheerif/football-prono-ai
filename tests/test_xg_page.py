import unittest

import pandas as pd

from pages import xg


class XgPageTests(unittest.TestCase):
    def setUp(self):
        self.frame = pd.DataFrame(
            [
                {
                    "fixture_id": 1,
                    "season": 2026,
                    "date": pd.Timestamp("2026-09-01", tz="UTC"),
                    "home_team_id": 10,
                    "away_team_id": 20,
                    "home_name": "Paris",
                    "away_name": "Marseille",
                    "home_goals": 2,
                    "away_goals": 1,
                    "home_xg": 1.8,
                    "away_xg": 0.7,
                    "home_retrieved_at": "2026-09-02T00:00:00",
                    "away_retrieved_at": "2026-09-02T00:00:00",
                    "home_payload_sha256": "home-1",
                    "away_payload_sha256": "away-1",
                    "xg_complete": True,
                },
                {
                    "fixture_id": 2,
                    "season": 2026,
                    "date": pd.Timestamp("2026-09-08", tz="UTC"),
                    "home_team_id": 30,
                    "away_team_id": 10,
                    "home_name": "Lyon",
                    "away_name": "Paris",
                    "home_goals": 1,
                    "away_goals": 1,
                    "home_xg": 1.1,
                    "away_xg": 1.4,
                    "home_retrieved_at": "2026-09-09T00:00:00",
                    "away_retrieved_at": "2026-09-09T00:00:00",
                    "home_payload_sha256": "home-2",
                    "away_payload_sha256": "away-2",
                    "xg_complete": True,
                },
            ]
        )

    def test_team_view_normalizes_home_and_away_matches(self):
        view = xg._team_view(self.frame, 10, limit=8)

        self.assertEqual(len(view), 2)
        self.assertEqual(view.iloc[0]["Adversaire"], "Lyon")
        self.assertEqual(float(view.iloc[0]["xG"]), 1.4)
        self.assertEqual(float(view.iloc[0]["xGA"]), 1.1)
        self.assertEqual(float(view.iloc[1]["xG"]), 1.8)

    def test_seasonal_coverage_distinguishes_complete_and_missing_xg(self):
        incomplete = self.frame.iloc[[0]].copy()
        incomplete["fixture_id"] = 3
        incomplete["xg_complete"] = False
        frame = pd.concat([self.frame, incomplete], ignore_index=True)

        coverage = xg._coverage_by_season(frame)

        self.assertEqual(int(coverage.iloc[0]["matchs_termines"]), 3)
        self.assertEqual(int(coverage.iloc[0]["matchs_xg"]), 2)
        self.assertEqual(float(coverage.iloc[0]["couverture"]), 66.7)


if __name__ == "__main__":
    unittest.main()
