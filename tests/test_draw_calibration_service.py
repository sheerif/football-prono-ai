import unittest

from services import draw_calibration_service, probability_validation


class DrawCalibrationServiceTests(unittest.TestCase):
    def sample_prediction(self):
        return {
            "expected_home_goals": 1.3,
            "expected_away_goals": 1.1,
            "probabilities": {
                "home_probability": 45.0,
                "draw_probability": 25.0,
                "away_probability": 30.0,
            },
            "matrix": [
                {"Score": "0-0", "Buts domicile": 0, "Buts extérieur": 0, "Probabilité": 10.0},
                {"Score": "1-1", "Buts domicile": 1, "Buts extérieur": 1, "Probabilité": 15.0},
                {"Score": "1-0", "Buts domicile": 1, "Buts extérieur": 0, "Probabilité": 25.0},
                {"Score": "2-0", "Buts domicile": 2, "Buts extérieur": 0, "Probabilité": 10.0},
                {"Score": "2-1", "Buts domicile": 2, "Buts extérieur": 1, "Probabilité": 10.0},
                {"Score": "0-1", "Buts domicile": 0, "Buts extérieur": 1, "Probabilité": 20.0},
                {"Score": "0-2", "Buts domicile": 0, "Buts extérieur": 2, "Probabilité": 10.0},
            ],
        }

    def test_calibration_updates_distribution_and_score_matrix_together(self):
        result, metadata = draw_calibration_service.calibrate_score_prediction(
            self.sample_prediction()
        )

        self.assertTrue(metadata["applied"])
        self.assertEqual(metadata["model_version"], draw_calibration_service.MODEL_VERSION)
        self.assertNotEqual(
            result["probabilities"]["draw_probability"], 25.0
        )
        probability_validation.validate_distribution(result["probabilities"])
        probability_validation.validate_score_matrix(
            result["matrix"], result["probabilities"]
        )

        derived_draw = sum(
            row["Probabilité"]
            for row in result["matrix"]
            if row["Buts domicile"] == row["Buts extérieur"]
        )
        self.assertAlmostEqual(
            derived_draw,
            result["probabilities"]["draw_probability"],
            delta=0.08,
        )

    def test_missing_matrix_fails_open_without_changing_probabilities(self):
        original = {
            "probabilities": {
                "home_probability": 40.0,
                "draw_probability": 25.0,
                "away_probability": 35.0,
            }
        }
        result, metadata = draw_calibration_service.calibrate_score_prediction(
            original
        )
        self.assertFalse(metadata["applied"])
        self.assertEqual(result, original)

    def test_invalid_matrix_is_not_calibrated(self):
        original = {
            "expected_home_goals": 1.2,
            "expected_away_goals": 1.0,
            "probabilities": {
                "home_probability": 40.0,
                "draw_probability": 25.0,
                "away_probability": 35.0,
            },
            "matrix": [{"Probabilité": 100.0}],
        }
        result, metadata = draw_calibration_service.calibrate_score_prediction(
            original
        )
        self.assertFalse(metadata["applied"])
        self.assertEqual(result, original)


if __name__ == "__main__":
    unittest.main()
