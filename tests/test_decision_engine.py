import unittest

import pandas as pd

from services import decision_engine, prediction_service


class DecisionEngineTests(unittest.TestCase):
    def test_double_chances_are_derived_from_the_single_distribution(self):
        chances = decision_engine.compute_double_chances(
            {"home_probability": 48, "draw_probability": 27, "away_probability": 25}
        )
        self.assertEqual(chances, {"1X": 75.0, "X2": 52.0, "12": 73.0})

    def test_market_advice_does_not_create_a_fake_distribution(self):
        result = decision_engine.calculate(
            {"home_probability": 39, "draw_probability": 27, "away_probability": 34},
            data_quality=0.9,
            stability_score=0.9,
            api_refinement={"applied": True, "api_probabilities": [38, 29, 33]},
        )
        prediction = result["prediction"]
        self.assertAlmostEqual(
            prediction["home_probability"]
            + prediction["draw_probability"]
            + prediction["away_probability"],
            100.0,
        )
        self.assertNotEqual(prediction["away_probability"], 0)
        self.assertIn(prediction["recommended_market"], {"12", "PRUDENCE"})

    def test_divergent_sources_increase_risk_and_avoid_forced_win(self):
        result = decision_engine.calculate(
            {"home_probability": 40, "draw_probability": 29, "away_probability": 31},
            data_quality=0.55,
            stability_score=0.55,
            ai_primary={"home_probability": 20, "draw_probability": 25, "away_probability": 55},
            ai_secondary={"home_probability": 25, "draw_probability": 30, "away_probability": 45},
        )
        self.assertEqual(result["consensus"]["level"], "divergence")
        self.assertIn(result["risk"]["level"], {"modéré", "élevé"})
        self.assertNotEqual(result["recommendation"]["market"], "1")

    def test_score_matrix_sums_to_the_same_1n2_distribution(self):
        matches = pd.DataFrame(
            [
                {"date": "2025-01-01", "season": 2024, "home_team_id": 1, "away_team_id": 3, "home_goals": 2, "away_goals": 0},
                {"date": "2025-01-02", "season": 2024, "home_team_id": 2, "away_team_id": 1, "home_goals": 1, "away_goals": 1},
                {"date": "2025-01-03", "season": 2024, "home_team_id": 2, "away_team_id": 3, "home_goals": 0, "away_goals": 1},
                {"date": "2025-01-04", "season": 2024, "home_team_id": 1, "away_team_id": 2, "home_goals": 3, "away_goals": 1},
            ]
        )
        result = prediction_service.predict_scorelines(matches, 1, 2, max_goals=6)
        matrix = result["matrix"]
        derived = [
            sum(row["Probabilité"] for row in matrix if row["Buts domicile"] > row["Buts extérieur"]),
            sum(row["Probabilité"] for row in matrix if row["Buts domicile"] == row["Buts extérieur"]),
            sum(row["Probabilité"] for row in matrix if row["Buts domicile"] < row["Buts extérieur"]),
        ]
        expected = result["probabilities"]
        self.assertAlmostEqual(sum(expected.values()), 100.0, places=2)
        self.assertAlmostEqual(derived[0], expected["home_probability"], places=1)
        self.assertAlmostEqual(derived[1], expected["draw_probability"], places=1)
        self.assertAlmostEqual(derived[2], expected["away_probability"], places=1)


if __name__ == "__main__":
    unittest.main()
