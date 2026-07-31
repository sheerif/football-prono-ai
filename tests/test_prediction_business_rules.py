import unittest

import pandas as pd

from services import prediction_helpers, prediction_service


class PredictionBusinessRuleTests(unittest.TestCase):
    def test_api_probabilities_refine_without_overriding_internal_model(self):
        refined, details = prediction_service.blend_with_api_prediction(
            {
                "home_probability": 50,
                "draw_probability": 25,
                "away_probability": 25,
                "confidence": 50,
            },
            {
                "fixture_id": 123,
                "home_probability": 30,
                "draw_probability": 30,
                "away_probability": 40,
            },
            api_weight=0.20,
        )

        self.assertEqual(
            [
                refined["home_probability"],
                refined["draw_probability"],
                refined["away_probability"],
            ],
            [46.0, 26.0, 28.0],
        )
        self.assertTrue(details["applied"])
        self.assertFalse(details["agreement"])
        self.assertEqual(details["api_weight"], 0.20)

    def test_invalid_api_probabilities_leave_prediction_unchanged(self):
        base = {
            "home_probability": 40,
            "draw_probability": 30,
            "away_probability": 30,
            "confidence": 40,
        }
        refined, details = prediction_service.blend_with_api_prediction(
            base,
            {"home_probability": None, "draw_probability": 30, "away_probability": 70},
        )

        self.assertFalse(details["applied"])
        self.assertEqual(refined["home_probability"], 40)

    def test_draw_probability_is_anchored_near_observed_rate(self):
        prediction = prediction_service.predict_simple(
            0.5,
            0.5,
            draw_factor=0.246,
        )

        self.assertAlmostEqual(
            prediction["home_probability"]
            + prediction["draw_probability"]
            + prediction["away_probability"],
            100.0,
            places=2,
        )
        self.assertGreaterEqual(prediction["draw_probability"], 24.0)
        self.assertLessEqual(prediction["draw_probability"], 28.0)

    def test_draw_probability_falls_when_strength_gap_grows(self):
        balanced = prediction_service.predict_simple(0.7, 0.7)
        unbalanced = prediction_service.predict_simple(1.2, 0.2)

        self.assertGreater(
            balanced["draw_probability"],
            unbalanced["draw_probability"],
        )

    def test_rankings_can_only_use_real_upcoming_fixtures(self):
        matches = pd.DataFrame(
            [
                {
                    "fixture_id": 1,
                    "date": "2026-08-03T18:00:00Z",
                    "home_team_id": 10,
                    "away_team_id": 20,
                    "home_goals": None,
                    "away_goals": None,
                },
                {
                    "fixture_id": 2,
                    "date": "2026-08-04T18:00:00Z",
                    "home_team_id": 20,
                    "away_team_id": 30,
                    "home_goals": None,
                    "away_goals": None,
                },
                {
                    "fixture_id": 3,
                    "date": "2026-07-20T18:00:00Z",
                    "home_team_id": 10,
                    "away_team_id": 20,
                    "home_goals": 1,
                    "away_goals": 0,
                },
            ]
        )

        fixtures = prediction_helpers.upcoming_fixtures(
            matches,
            team_ids=[10, 20],
            now="2026-07-31T00:00:00Z",
            days_ahead=30,
        )

        self.assertEqual(fixtures["fixture_id"].tolist(), [1])

    def test_historical_context_excludes_future_results(self):
        matches = pd.DataFrame(
            [
                {
                    "fixture_id": 1,
                    "date": "2026-07-01T18:00:00Z",
                    "home_goals": 1,
                    "away_goals": 1,
                },
                {
                    "fixture_id": 2,
                    "date": "2026-08-05T18:00:00Z",
                    "home_goals": 2,
                    "away_goals": 0,
                },
            ]
        )

        context = prediction_helpers.historical_context_before(
            matches,
            "2026-08-01T18:00:00Z",
        )

        self.assertEqual(context["fixture_id"].tolist(), [1])


if __name__ == "__main__":
    unittest.main()
