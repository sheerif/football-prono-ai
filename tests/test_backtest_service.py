import unittest

import pandas as pd

from services import backtest_service


def _dataset(api_updated_at=None):
    rows = []
    start = pd.Timestamp("2023-01-01T12:00:00Z")
    for index in range(36):
        home = 10 if index % 2 == 0 else 20
        away = 20 if home == 10 else 10
        rows.append(
            {
                "fixture_id": index + 1,
                "league_id": 61,
                "season": 2023 if index < 30 else 2024,
                "date": (start + pd.Timedelta(days=index)).isoformat(),
                "home_team_id": home,
                "away_team_id": away,
                "home_goals": index % 3,
                "away_goals": (index + 1) % 3,
                "api_advice": "Victoire domicile" if index == 35 else None,
                "api_winner": "Domicile" if index == 35 else None,
                "api_home_probability": 60 if index == 35 else None,
                "api_draw_probability": 20 if index == 35 else None,
                "api_away_probability": 20 if index == 35 else None,
                "api_total_home": "60%" if index == 35 else None,
                "api_total_away": "40%" if index == 35 else None,
                "api_raw_json": "{}" if index == 35 else None,
                "api_updated_at": api_updated_at if index == 35 else None,
            }
        )
    return pd.DataFrame(rows)


class BacktestServiceTests(unittest.TestCase):
    def test_backtest_uses_only_strictly_earlier_context(self):
        result = backtest_service.run(
            _dataset("2023-02-04T12:00:00Z"),
            start_season=2024,
            min_prior_matches=30,
        )
        self.assertEqual(result["new_draw_formula"]["matches"], 6)
        self.assertEqual(
            result["api_comparable_subset"]["combined_api_football"]["matches"],
            1,
        )
        self.assertEqual(result["post_kickoff_api_predictions_excluded"], 0)

    def test_post_kickoff_api_prediction_is_excluded(self):
        result = backtest_service.run(
            _dataset("2023-02-06T12:00:00Z"),
            start_season=2024,
            min_prior_matches=30,
        )
        self.assertEqual(
            result["api_comparable_subset"]["combined_api_football"]["matches"],
            0,
        )
        self.assertEqual(result["post_kickoff_api_predictions_excluded"], 1)


if __name__ == "__main__":
    unittest.main()
