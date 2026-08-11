import unittest

from services import pdf_report_service


class PdfReportServiceTests(unittest.TestCase):
    def test_round_labels_are_readable(self):
        self.assertEqual(
            pdf_report_service.round_label("Regular Season - 12"), "Journée 12"
        )
        self.assertEqual(pdf_report_service.round_label(""), "Journée non précisée")

    def test_pdf_contains_a_visual_report_for_available_and_missing_data(self):
        pdf = pdf_report_service.build_pdf(
            [
                {
                    "available": True,
                    "home_name": "Marseille",
                    "away_name": "Strasbourg",
                    "date": "21/08/2026 18:45 UTC",
                    "actual_score": "À venir",
                    "home_probability": 45.0,
                    "draw_probability": 27.0,
                    "away_probability": 28.0,
                    "score_probable": "1-1",
                    "expected_home_goals": 1.42,
                    "expected_away_goals": 1.06,
                    "solidity": 71.0,
                    "risk": "modéré",
                    "market": "1X",
                },
                {
                    "available": False,
                    "home_name": "Equipe A",
                    "away_name": "Equipe B",
                    "date": "22/08/2026 18:45 UTC",
                    "actual_score": "À venir",
                    "reason": "Historique insuffisant avant ce match.",
                },
            ],
            league="Ligue 1",
            season="2026-2027",
            round_name="Journée 1",
        )
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(pdf), 1500)


if __name__ == "__main__":
    unittest.main()
