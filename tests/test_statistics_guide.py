from pathlib import Path
import unittest

from components import statistics_guide


ROOT = Path(__file__).resolve().parents[1]


class StatisticsGuideTests(unittest.TestCase):
    def test_every_prediction_tab_has_a_structured_legend(self):
        sections = {
            "overview",
            "form",
            "lineups",
            "h2h",
            "statistics",
            "prediction",
        }
        self.assertEqual(set(statistics_guide.GLOSSARY), sections)
        for section in sections:
            self.assertTrue(statistics_guide.GLOSSARY[section])
            self.assertTrue(
                all(len(entry) == 4 for entry in statistics_guide.GLOSSARY[section])
            )

    def test_ambiguous_metrics_have_explicit_distinctions(self):
        all_text = " ".join(
            value
            for entries in statistics_guide.GLOSSARY.values()
            for entry in entries
            for value in entry
        ).casefold()
        for expected in (
            "ce n’est pas une probabilité",
            "ce ne sont pas les xg observés",
            "somme = 100 %",
            "avant le coup d’envoi",
        ):
            self.assertIn(expected, all_text)

    def test_both_prediction_pages_render_all_contextual_legends(self):
        for relative_path in ("pages/matchs_a_venir.py", "pages/analyse_match.py"):
            source = (ROOT / relative_path).read_text(encoding="utf-8")
            for section in statistics_guide.GLOSSARY:
                self.assertIn(f'statistics_guide.render("{section}")', source)

    def test_persistent_documentation_covers_method_and_limits(self):
        documentation = (ROOT / "docs/statistics-glossary.md").read_text(
            encoding="utf-8"
        )
        for heading in (
            "## Règles générales",
            "## Expected Goals",
            "## Probabilités et score",
            "## Qualité et décision",
            "## Joueurs et tactique",
            "## Provenance",
        ):
            self.assertIn(heading, documentation)


if __name__ == "__main__":
    unittest.main()
