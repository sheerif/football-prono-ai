import datetime

import pandas as pd
import streamlit as st

from components import ui
from database.database import engine
from services import import_service
from services.season_format import season_period, season_range


LEAGUE_PRESETS = {
    "Europe - Ligue des Champions": 2,
    "France - Ligue 1": 61,
    "Angleterre - Premier League": 39,
    "Espagne - La Liga": 140,
    "Italie - Serie A": 135,
    "Allemagne - Bundesliga": 78,
}


def _summary_counts() -> dict[str, int]:
    try:
        leagues = pd.read_sql("SELECT COUNT(*) AS count FROM leagues", engine).iloc[0]["count"]
        teams = pd.read_sql("SELECT COUNT(*) AS count FROM teams", engine).iloc[0]["count"]
        matches = pd.read_sql("SELECT COUNT(*) AS count FROM matches", engine).iloc[0]["count"]
        standings = pd.read_sql("SELECT COUNT(*) AS count FROM standings", engine).iloc[0]["count"]
    except Exception:
        leagues = teams = matches = standings = 0
    return {
        "leagues": int(leagues),
        "teams": int(teams),
        "matches": int(matches),
        "standings": int(standings),
    }


def show():
    ui.page_hero(
        "Traitement des données",
        "Importez ou mettez à jour les championnats, saisons sportives, équipes et standings utilisés par les analyses.",
    )

    counts = _summary_counts()
    ui.section_label("État actuel")
    cols = st.columns(4)
    cols[0].metric("Championnats", counts["leagues"])
    cols[1].metric("Équipes", counts["teams"])
    cols[2].metric("Matchs", counts["matches"])
    cols[3].metric("Classements", counts["standings"])

    ui.section_label("Importer / mettre à jour")
    st.info("Cet écran sert à peupler la base SQLite et à rafraîchir les indicateurs du tableau de bord.")
    max_season = max(2026, import_service.get_auto_refresh_config()["end_season"])

    with st.container(border=True):
        selected_presets = st.multiselect(
            "Ligues à traiter",
            options=list(LEAGUE_PRESETS.keys()),
            default=list(LEAGUE_PRESETS.keys()),
            key="data_presets",
        )

        col1, col2, col3 = st.columns(3)
        start_season = col1.number_input("Saison sportive de début", min_value=2016, max_value=max_season, value=2016, step=1)
        end_season = col2.number_input("Saison sportive de fin", min_value=2016, max_value=max_season, value=max_season, step=1)
        pause = col3.number_input("Pause entre requêtes (s)", min_value=0.5, max_value=10.0, value=2.0, step=0.5)
        st.caption(f"Période sélectionnée: {season_period(start_season)} à {season_period(end_season)}")

        max_retries = st.slider("Nombre maximal de tentatives", min_value=1, max_value=10, value=6)

        quick_cols = st.columns([1, 1])
        quick_import_l1 = quick_cols[0].button("Import rapide Ligue 1", width="stretch")
        quick_import_6 = quick_cols[1].button("Import LDC + 5 championnats", width="stretch")

        launch = st.button("Lancer le traitement", type="primary", width="stretch")

    if quick_import_l1:
        selected_presets = ["France - Ligue 1"]
    elif quick_import_6:
        selected_presets = list(LEAGUE_PRESETS.keys())

    if launch or quick_import_l1 or quick_import_6:
        if start_season > end_season:
            st.error("La saison sportive de début doit être inférieure ou égale à la saison sportive de fin.")
            return

        league_ids = [LEAGUE_PRESETS[label] for label in selected_presets]
        seasons = list(range(int(start_season), int(end_season) + 1))

        st.info("Traitement en cours. Ne rechargez pas la page pendant l’import.")
        progress = st.progress(0)
        status = st.empty()
        started_at = datetime.datetime.now(datetime.UTC).replace(tzinfo=None).isoformat()

        try:
            status.write("Initialisation de la base...")
            import_service.init_db()

            status.write(f"Import de {len(league_ids)} championnat(s) sur {len(seasons)} saison(s) sportive(s): {season_range(seasons)}...")
            import_service.import_leagues_cautious(
                league_ids,
                seasons=seasons,
                pause=float(pause),
                max_retries=int(max_retries),
            )
            progress.progress(100)
            st.success("Traitement terminé.")
            st.session_state["last_import_ok"] = True
            import_service.record_update_log(
                event_type="import_manuel",
                status="effectuée",
                started_at=started_at,
                reason="Import manuel terminé.",
                leagues=league_ids,
                seasons=seasons,
                details={
                    "selected_presets": selected_presets,
                    "pause": float(pause),
                    "max_retries": int(max_retries),
                    "counts_after": _summary_counts(),
                },
            )
        except Exception as exc:
            st.session_state["last_import_ok"] = False
            st.error(f"Erreur pendant le traitement: {exc}")
            import_service.record_update_log(
                event_type="import_manuel",
                status="erreur",
                started_at=started_at,
                reason="Erreur pendant l’import manuel.",
                leagues=league_ids,
                seasons=seasons,
                details={
                    "selected_presets": selected_presets,
                    "pause": float(pause),
                    "max_retries": int(max_retries),
                },
                error=str(exc),
            )
        finally:
            counts_after = _summary_counts()
            st.markdown("### État après traitement")
            cols2 = st.columns(4)
            cols2[0].metric("Championnats", counts_after["leagues"])
            cols2[1].metric("Équipes", counts_after["teams"])
            cols2[2].metric("Matchs", counts_after["matches"])
            cols2[3].metric("Classements", counts_after["standings"])

    st.markdown("---")
    st.caption("Astuce: si vous voyez des 429, augmentez la pause à 3-5 secondes.")


if __name__ == "__main__":
    ui.run_direct_page("Traitement des données", show)
