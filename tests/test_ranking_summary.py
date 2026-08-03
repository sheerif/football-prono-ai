import unittest
from unittest.mock import Mock, patch

from components import ranking_summary


class RankingSummaryTests(unittest.TestCase):
    def test_ranking_is_displayed_as_an_index_not_a_probability(self):
        columns = [Mock() for _ in range(4)]
        with patch.object(ranking_summary.st, "columns", return_value=columns):
            ranking_summary.render(
                {
                    "confidence": 57,
                    "ranking_score": 72,
                    "data_quality": 0.91,
                    "margin": 18,
                }
            )

        columns[0].metric.assert_called_once_with(
            "Probabilité scénario principal", "57 %"
        )
        columns[1].metric.assert_called_once_with(
            "Indice de solidité", "72 / 100"
        )
        columns[2].metric.assert_called_once_with(
            "Qualité des données", "91 / 100"
        )
        columns[3].metric.assert_called_once_with("Marge", "18 points")


if __name__ == "__main__":
    unittest.main()
