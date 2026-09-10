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
        self.assertNotIn("st.iframe(_widget_html(widget_key), height=880, width=780)", source)
        self.assertIn('st.iframe(_widget_html(widget_key), height=880, width="stretch")', source)
        self.assertIn('os.getenv("API_FOOTBALL_WIDGET_KEY"', source)
        self.assertIn('os.getenv("API_FOOTBALL_KEY"', source)

    def test_mobile_columns_stack_and_media_is_present(self):
        css = (ROOT / "components" / "ui.py").read_text(encoding="utf-8")
        self.assertIn("@media (max-width: 600px)", css)
        self.assertIn("flex: 1 1 100% !important", css)
        self.assertIn("max-width: 100% !important", css)
        self.assertIn("background: rgba(248, 251, 252, .86)", css)

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

    def test_background_progress_panels_refresh_automatically(self):
        sidebar_source = (ROOT / "components" / "ui.py").read_text(encoding="utf-8")
        updates_source = (ROOT / "pages" / "data_management.py").read_text(
            encoding="utf-8"
        )
        decorator = '@st.fragment(run_every="1s")'
        self.assertIn(f"{decorator}\ndef render_background_jobs", sidebar_source)
        self.assertIn(f"{decorator}\ndef _render_jobs", updates_source)
        self.assertIn("progress_download_caption(job)", sidebar_source)
        self.assertIn("ui.progress_download_caption(job)", updates_source)
        self.assertIn("text=progress_bar_text(job)", sidebar_source)
        self.assertIn("text=ui.progress_bar_text(job)", updates_source)
        self.assertIn('job.get("status") == "partial"', sidebar_source)
        self.assertIn("st.warning(job.get(\"message\")", updates_source)

    def test_every_long_user_action_has_visible_progress(self):
        required_markers = {
            "pages/prediction_ia.py": (
                "Préparation de la prédiction",
                "Préparation du classement",
            ),
            "pages/analyse_match.py": ("Préparation de l’analyse",),
            "pages/rapports_pdf.py": ("Préparation du rapport",),
            "pages/joueurs.py": ("Préparation…",),
            "pages/matchs_a_venir.py": (
                "Préparation des compositions et performances",
                "Préparation du téléchargement",
                "Préparation du rapport",
            ),
            "pages/data_management.py": ("ui.progress_bar_text(job)",),
        }
        for relative_path, markers in required_markers.items():
            source = (ROOT / relative_path).read_text(encoding="utf-8")
            for marker in markers:
                self.assertIn(marker, source, f"{relative_path}: {marker}")

    def test_xg_page_is_registered_in_the_custom_navigation(self):
        sidebar = (ROOT / "components" / "sidebar.py").read_text(encoding="utf-8")
        ui_source = (ROOT / "components" / "ui.py").read_text(encoding="utf-8")
        page = (ROOT / "pages" / "xg.py").read_text(encoding="utf-8")

        self.assertIn('"xG": "pages/xg.py"', sidebar)
        self.assertIn('"xG": "xG"', ui_source)
        self.assertIn('ui.run_direct_page("xG", show)', page)


if __name__ == "__main__":
    unittest.main()
