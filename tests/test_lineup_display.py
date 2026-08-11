import unittest

import pandas as pd

from components import tactical
from pages import matchs_a_venir


class LineupDisplayTests(unittest.TestCase):
    def test_jersey_numbers_are_rendered_as_integers(self):
        values = [25.0, "11.0", 8, None, float("nan")]
        expected = [25, 11, 8, "-", "-"]
        self.assertEqual(
            [matchs_a_venir._lineup_number(value) for value in values],
            expected,
        )
        self.assertEqual(
            [tactical._player_number(value) for value in values],
            expected,
        )

    def test_substitutes_table_keeps_integer_numbers(self):
        table = matchs_a_venir._lineup_table(
            [
                {
                    "number": 25.0,
                    "player_name": "A. Rabiot",
                    "position": "M",
                    "form_rating": 6.3,
                }
            ]
        )
        self.assertEqual(table.iloc[0]["N°"], 25)
        self.assertFalse(pd.isna(table.iloc[0]["N°"]))


if __name__ == "__main__":
    unittest.main()
