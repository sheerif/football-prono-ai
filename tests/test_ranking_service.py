import unittest

from services import prediction_service, ranking_service


class RankingServiceTests(unittest.TestCase):
    def test_formula_uses_the_documented_weights(self):
        result = ranking_service.compute_ranking_score(
            [57, 25, 18],
            data_quality=0.91,
            stability_score=1.0,
            agreement_score=1.0,
        )
        expected = 100 * (
            0.40 * 0.57
            + 0.20 * min(1.0, 32 / 25)
            + 0.15 * 0.91
            + 0.15 * 1.0
            + 0.10 * 1.0
        )
        self.assertAlmostEqual(result["ranking_score"], expected, places=2)
        self.assertEqual(result["margin"], 32.0)
        self.assertEqual(result["margin_score"], 1.0)

    def test_calibrator_is_an_optional_extension_point(self):
        result = ranking_service.compute_ranking_score(
            [60, 25, 15],
            data_quality=1,
            stability_score=1,
            agreement_score=1,
            calibrator=lambda probability: probability - 0.1,
        )
        self.assertEqual(result["calibrated_probability"], 0.5)

    def test_stability_rewards_the_same_favorite_on_all_windows(self):
        stable = ranking_service.compute_stability(
            [[55, 25, 20], [51, 29, 20], [48, 30, 22]]
        )
        unstable = ranking_service.compute_stability(
            [[55, 25, 20], [30, 45, 25], [25, 30, 45]]
        )
        self.assertEqual(stable, 1.0)
        self.assertLess(unstable, stable)

    def test_agreement_scores_follow_the_business_rule(self):
        self.assertEqual(ranking_service.compute_agreement(None), 0.75)
        self.assertEqual(
            ranking_service.compute_agreement(
                {"applied": True, "agreement": True}
            ),
            1.0,
        )
        self.assertEqual(
            ranking_service.compute_agreement(
                {"applied": True, "agreement": False}
            ),
            0.60,
        )

    def test_prediction_probabilities_and_confidence_remain_unchanged(self):
        prediction = prediction_service.predict_simple(0.8, 0.5)
        probabilities = [
            prediction["home_probability"],
            prediction["draw_probability"],
            prediction["away_probability"],
        ]
        self.assertAlmostEqual(sum(probabilities), 100.0, places=2)
        self.assertEqual(prediction["confidence"], max(probabilities))
        self.assertIn("ranking_score", prediction)
        self.assertGreaterEqual(prediction["ranking_score"], 0)
        self.assertLessEqual(prediction["ranking_score"], 100)

    def test_data_quality_is_bounded(self):
        full = ranking_service.compute_data_quality(
            historical_match_count=100,
            statistics_coverage=True,
            lineup_coverage=1,
            freshness=2,
            api_coverage=1,
        )
        empty = ranking_service.compute_data_quality(
            historical_match_count=-1,
            statistics_coverage=False,
            lineup_coverage=0,
            freshness=-2,
            api_coverage=0,
        )
        self.assertEqual(full, 1.0)
        self.assertEqual(empty, 0.0)


if __name__ == "__main__":
    unittest.main()
