import unittest
from unittest.mock import patch

import pandas as pd

from services import cross_insight_service, final_prediction_service, prediction_helpers, prediction_service


class PredictionBusinessRuleTests(unittest.TestCase):
    def test_load_matches_still_returns_selected_seasons(self):
        expected = pd.DataFrame([{"fixture_id": 1}])
        with patch.object(pd, "read_sql", return_value=expected) as read_sql:
            result = prediction_helpers.load_matches(61, [2024, 2025])
        self.assertIs(result, expected)
        params = read_sql.call_args.kwargs["params"]
        self.assertEqual(params, {"lid": 61, "s0": 2024, "s1": 2025})

    def test_shared_final_service_applies_api_once_and_owns_scenario(self):
        internal = {
            "home_probability": 50,
            "draw_probability": 25,
            "away_probability": 25,
            "confidence": 50,
        }
        details = {"home_form_score": 60, "away_form_score": 40}
        with (
            patch.object(
                prediction_helpers,
                "predict_match",
                return_value=(internal, {"played": 10}, {"played": 10}, details),
            ),
            patch.object(
                prediction_service,
                "blend_with_api_prediction",
                wraps=prediction_service.blend_with_api_prediction,
            ) as blend,
            patch.object(
                prediction_service,
                "predict_scorelines",
                return_value={"scores": []},
            ),
        ):
            result = final_prediction_service.calculate(
                pd.DataFrame(),
                1,
                2,
                "Domicile",
                "Extérieur",
                api_signal={
                    "advice": "Victoire domicile",
                    "home_probability": 60,
                    "draw_probability": 20,
                    "away_probability": 20,
                },
            )
        blend.assert_called_once()
        self.assertEqual(
            result["model_details"]["api_refinement"],
            result["api_refinement"],
        )
        self.assertEqual(
            result["model_details"]["consensus_advice"],
            result["consensus_advice"],
        )

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
                "advice": "Double chance : nul ou extérieur",
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

    def test_neutral_api_advice_is_ignored(self):
        base = {
            "home_probability": 45,
            "draw_probability": 28,
            "away_probability": 27,
            "confidence": 45,
        }
        refined, details = prediction_service.blend_with_api_prediction(
            base,
            {
                "advice": "No predictions available",
                "home_probability": 33,
                "draw_probability": 33,
                "away_probability": 33,
            },
        )

        self.assertFalse(details["applied"])
        self.assertEqual(refined["home_probability"], 45)
        self.assertIn("neutre", details["ignored_reason"])

    def test_tied_api_scenarios_can_agree_with_internal_pick(self):
        refined, details = prediction_service.blend_with_api_prediction(
            {
                "home_probability": 48,
                "draw_probability": 28,
                "away_probability": 24,
                "confidence": 48,
            },
            {
                "advice": "Double chance : domicile ou nul",
                "home_probability": 50,
                "draw_probability": 50,
                "away_probability": 0,
                "comparison": {
                    "Indice global API": {"home": 60, "away": 40, "edge": 0.2}
                },
            },
        )

        self.assertTrue(details["applied"])
        self.assertTrue(details["agreement"])
        self.assertEqual(details["favored_outcomes"], ["1", "N"])
        self.assertGreater(details["api_weight"], 0.10)
        self.assertLessEqual(details["api_weight"], 0.30)
        advice = prediction_service.build_consensus_advice(
            refined,
            details,
            "Domicile FC",
            "Extérieur FC",
        )
        self.assertEqual(advice["level"], "convergence")
        self.assertIn("convergent", advice["message"])

    def test_api_raw_comparison_is_exposed_as_statistics(self):
        signal = cross_insight_service._api_signal_from_row(
            {
                "fixture_id": 42,
                "date": "2026-08-01",
                "advice": "Double chance",
                "winner": "Domicile FC",
                "home_probability": 50,
                "draw_probability": 50,
                "away_probability": 0,
                "total_home": "60%",
                "total_away": "40%",
                "updated_at": "2026-07-31",
                "raw_json": '{"comparison":{"form":{"home":"70%","away":"30%"},"total":{"home":"60%","away":"40%"}}}',
            }
        )

        self.assertEqual(signal["comparison"]["Forme API"]["edge"], 0.4)
        self.assertEqual(signal["comparison"]["Indice global API"]["home"], 60)

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
