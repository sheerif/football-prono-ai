import html
import os

import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv

from components import ui
from services import prediction_helpers


load_dotenv()


LEAGUES = {
    "Toutes les ligues": None,
    "Ligue 1": 61,
    "Premier League": 39,
    "La Liga": 140,
    "Serie A": 135,
    "Bundesliga": 78,
    "Ligue des Champions": 2,
}

WIDGETS = {
    "Ligues": "leagues",
    "Matchs": "games",
    "Calendrier ligue": "league",
    "Classement": "standings",
}

LANGUAGES = {
    "Français": "fr",
    "Anglais": "en",
    "Espagnol": "es",
    "Italien": "it",
    "Allemand": "de",
}

THEMES = {
    "White": "white",
    "Grey": "grey",
    "Dark": "dark",
    "Blue": "blue",
}


def _league_options() -> dict[str, int | None]:
    leagues = prediction_helpers.fetch_leagues()
    if leagues.empty:
        return LEAGUES
    options = {"Toutes les ligues": None}
    for row in leagues.itertuples():
        country = f" - {row.country}" if getattr(row, "country", None) else ""
        options[f"{row.name}{country}"] = int(row.id)
    return options


def _season_options() -> list[int]:
    seasons = sorted(prediction_helpers.configured_seasons(), reverse=True)
    if seasons:
        return seasons
    return [2026]


def _attr(name: str, value) -> str:
    if value is None or value == "":
        return ""
    return f' {name}="{html.escape(str(value), quote=True)}"'


def _widget_attrs(
    widget_type: str,
    league_id: int | None,
    season: int,
    tab: str,
    refresh: int,
    target: str,
    show_logos: bool,
    show_favorites: bool,
) -> str:
    attrs = _attr("data-type", widget_type)
    if widget_type in {"games", "league", "standings"} and league_id is not None:
        attrs += _attr("data-league", league_id)
    if widget_type in {"league", "standings"}:
        attrs += _attr("data-season", season)
    if widget_type == "games":
        attrs += _attr("data-season", season)
        attrs += _attr("data-tab", tab)
        attrs += _attr("data-refresh", refresh)
        attrs += _attr("data-show-toolbar", "true")
        attrs += _attr("data-target-game", target)
        attrs += _attr("data-target-standings", target)
    if widget_type == "league":
        attrs += _attr("data-tab", tab)
        attrs += _attr("data-standings", "true")
    if widget_type == "standings":
        attrs += _attr("data-refresh", refresh)
    if widget_type == "leagues":
        attrs += _attr("data-target-league", target)
    attrs += _attr("data-show-logos", str(show_logos).lower())
    attrs += _attr("data-show-favorites", str(show_favorites).lower())
    return attrs


def _config_code(api_key: str, language: str, theme: str, timezone: str, show_errors: bool) -> str:
    return "\n".join(
        [
            "<!-- Configuration -->",
            '<api-sports-widget data-type="config"',
            f'  data-key="{api_key}"',
            '  data-sport="football"',
            f'  data-lang="{language}"',
            f'  data-theme="{theme}"',
            f'  data-timezone="{timezone}"',
            f'  data-show-errors="{str(show_errors).lower()}"',
            "></api-sports-widget>",
        ]
    )


def _generated_code(
    api_key: str,
    widget_type: str,
    league_id: int | None,
    season: int,
    tab: str,
    language: str,
    theme: str,
    timezone: str,
    refresh: int,
    target: str,
    show_logos: bool,
    show_favorites: bool,
    show_errors: bool,
) -> str:
    widget = f"<api-sports-widget{_widget_attrs(widget_type, league_id, season, tab, refresh, target, show_logos, show_favorites)}></api-sports-widget>"
    return f"{widget}\n\n{_config_code(api_key, language, theme, timezone, show_errors)}"


def _preview_html(code: str) -> str:
    return f"""
    <!doctype html>
    <html lang="fr">
      <head>
        <meta charset="utf-8" />
        <script type="module" src="https://widgets.api-sports.io/3.1.0/widgets.js"></script>
        <style>
          body {{
            margin: 0;
            background: #f5f7f2;
            font-family: Inter, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          }}
        </style>
      </head>
      <body>{code}</body>
    </html>
    """


def show():
    ui.page_hero(
        "Widgets Live",
        "Construisez, prévisualisez et copiez les widgets officiels API-Sports pour le football.",
    )

    env_key = os.getenv("API_FOOTBALL_KEY", "")
    if not env_key:
        st.error("API_FOOTBALL_KEY est absent du fichier .env. Ajoutez la clé API-Sports pour afficher les widgets.")
        return

    st.caption(
        "Mode local: clé chargée depuis .env. Documentation officielle: "
        "https://api-sports.io/documentation/widgets/v3"
    )

    left, right = st.columns([1, 1])
    league_options = _league_options()
    season_options = _season_options()

    with left:
        with st.container(border=True):
            st.subheader("Widgets Builder")
            st.caption("Choisissez un widget, ajustez ses options, puis prévisualisez le résultat.")
            st.link_button("Documentation API-Sports", "https://api-sports.io/documentation/widgets/v3")

            cols = st.columns(3)
            api_key = cols[0].text_input("API Key", value=env_key, type="password")
            language = LANGUAGES[cols[1].selectbox("Langue", list(LANGUAGES.keys()))]
            theme = THEMES[cols[2].selectbox("Thème", list(THEMES.keys()))]

            cols = st.columns(3)
            widget_label = cols[0].selectbox("Widget", list(WIDGETS.keys()))
            league_label = cols[1].selectbox("Championnat", list(league_options.keys()), index=1 if len(league_options) > 1 else 0)
            season = cols[2].selectbox("Saison", options=season_options, index=0)

            widget_type = WIDGETS[widget_label]
            league_id = league_options[league_label]

            timezone = st.text_input("Timezone", value="Europe/Paris")

            cols = st.columns(3)
            show_logos = cols[0].checkbox("Afficher logos", value=True)
            show_favorites = cols[1].checkbox("Favoris", value=False)
            show_errors = cols[2].checkbox("Debug erreurs", value=True)

            with st.expander("Paramètres spécifiques", expanded=True):
                if widget_type == "games":
                    tab = st.selectbox("Onglet matchs", ["all", "live", "finished", "scheduled"], index=0)
                elif widget_type == "league":
                    tab = st.selectbox("Onglet ligue", ["today", "results", "games", "standings"], index=3)
                else:
                    tab = "standings"
                refresh = st.number_input("Refresh en secondes", min_value=15, max_value=300, value=60, step=15)
                target = st.text_input("Target modal / sélecteur", value="modal")

    code = _generated_code(
        api_key,
        widget_type,
        league_id,
        int(season),
        tab,
        language,
        theme,
        timezone,
        int(refresh),
        target,
        show_logos,
        show_favorites,
        show_errors,
    )

    with right:
        generated_tab, preview_tab = st.tabs(["Code généré", "Preview"])
        with generated_tab:
            st.code(code, language="html")
            st.caption("Incluez la configuration une seule fois par page si vous copiez plusieurs widgets.")
        with preview_tab:
            components.html(_preview_html(code), height=760, scrolling=True)


if __name__ == "__main__":
    ui.run_direct_page("Widgets Live", show)
