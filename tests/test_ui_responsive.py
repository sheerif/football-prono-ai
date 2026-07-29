"""Fast, dependency-free smoke checks for the responsive UI contract.

These checks intentionally inspect the source contract rather than render a
browser. They run in CI even when Streamlit is not installed and catch the
fixed-width regressions that previously broke phones and tablets.
"""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ResponsiveUiContractTests(unittest.TestCase):
    def test_widget_is_not_fixed_to_780px(self):
        source = (ROOT / "pages" / "api_widgets.py").read_text(encoding="utf-8")
        self.assertNotIn("st.iframe(_widget_html(api_key), height=880, width=780)", source)
        self.assertIn('st.iframe(_widget_html(api_key), height=880, width="stretch")', source)

    def test_mobile_columns_stack_and_media_is_present(self):
        css = (ROOT / "components" / "ui.py").read_text(encoding="utf-8")
        self.assertIn("@media (max-width: 600px)", css)
        self.assertIn("flex: 1 1 100% !important", css)
        self.assertIn("max-width: 100% !important", css)

    def test_chart_summaries_are_rendered(self):
        files = [
            ROOT / "components" / "trends.py",
            ROOT / "pages" / "analyse_match.py",
            ROOT / "pages" / "matchs_a_venir.py",
            ROOT / "pages" / "joueurs.py",
        ]
        for path in files:
            self.assertIn("Résumé", path.read_text(encoding="utf-8"), str(path))

    def test_legacy_yellow_theme_tokens_are_absent(self):
        files = [ROOT / "components" / "ui.py", ROOT / "components" / "sidebar.py"]
        forbidden = ("#dcae4f", "#f1ca73", "rgba(220,174,79")
        for path in files:
            content = path.read_text(encoding="utf-8").lower().replace(" ", "")
            for token in forbidden:
                self.assertNotIn(token.replace(" ", ""), content, str(path))


if __name__ == "__main__":
    unittest.main()
