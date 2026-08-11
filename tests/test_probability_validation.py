import unittest

from services import probability_validation


class ProbabilityValidationTests(unittest.TestCase):
    def test_rejects_non_normalized_distribution(self):
        with self.assertRaises(ValueError):
            probability_validation.validate_distribution([50, 20, 20])

    def test_rejects_matrix_incompatible_with_distribution(self):
        matrix = [
            {"Buts domicile": 1, "Buts extérieur": 0, "Probabilité": 60},
            {"Buts domicile": 0, "Buts extérieur": 0, "Probabilité": 20},
            {"Buts domicile": 0, "Buts extérieur": 1, "Probabilité": 20},
        ]
        with self.assertRaises(ValueError):
            probability_validation.validate_score_matrix(
                matrix,
                {"home_probability": 50, "draw_probability": 30, "away_probability": 20},
            )
