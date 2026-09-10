import unittest
from unittest.mock import patch

import pandas as pd

from services import pdf_report_service


class PdfReportServiceTests(unittest.TestCase):
    def test_fixture_report_progress_tracks_each_match(self):
        fixtures = pd.DataFrame(
            [
                {
                    "fixture_id": 42,
                    "date": "2026-09-12T18:00:00",
                    "league_id": 61,
                    "league_name": "Ligue 1",
                    "api_round": "Regular Season - 4",
                    "home_name": "Paris",
                    "away_name": "Marseille",
                    "home_goals": None,
                    "away_goals": None,
                    "home_team_id": 1,
                    "away_team_id": 2,
                    "season": 2026,
                }
            ]
        )
        progress = []
        with patch.object(
            pdf_report_service,
            "historical_context",
            return_value=pd.DataFrame(),
        ):
            reports = pdf_report_service.build_fixture_reports(
                fixtures,
                progress_callback=lambda current, total, label: progress.append(
                    (current, total, label)
                ),
            )

        self.assertEqual(len(reports), 1)
        self.assertEqual(progress[0], (0, 1, "Préparation des matchs du rapport"))
        self.assertEqual(progress[-1], (1, 1, "Paris - Marseille traité"))

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
