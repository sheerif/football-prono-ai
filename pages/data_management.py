import pandas as pd
import streamlit as st

from components import ui
from database.database import engine
from services import background_jobs, import_service
from services.season_format import season_period, season_range


LEAGUE_PRESETS = {
    "Ligue des Champions": 2,
    "Ligue 1": 61,
    "Premier League": 39,
    "La Liga": 140,
    "Serie A": 135,
    "Bundesliga": 78,
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


def _format_datetime(value):
    timestamp = pd.to_datetime(value, errors="coerce")
    if pd.isna(timestamp):
        return value or "-"
    return timestamp.strftime("%d/%m/%Y %H:%M")


def _recent_logs(limit: int = 6) -> pd.DataFrame:
    try:
        logs = pd.read_sql(
            """
            SELECT event_type, status, started_at, finished_at, reason, error
            FROM update_log
            ORDER BY finished_at DESC, id DESC
            LIMIT :limit
            """,
            engine,
            params={"limit": int(limit)},
        )
    except Exception:
        return pd.DataFrame()
    if logs.empty:
        return logs
    return pd.DataFrame(
        [
            {
                "Type": row.event_type,
                "Statut": row.status,
                "Début": _format_datetime(row.started_at),
                "Fin": _format_datetime(row.finished_at),
                "Message": row.error or row.reason or "",
            }
            for row in logs.itertuples()
        ]
    )


def _render_jobs():
    jobs = background_jobs.list_jobs()
    active = [job for job in jobs if job.get("status") == "running"]
    finished = [job for job in jobs if job.get("status") != "running"][:5]

    ui.section_label("Téléchargements")
    if not active:
        st.info("Aucun téléchargement en cours.")
    for job in active:
        with st.container(border=True):
            st.markdown(f"### {job.get('label', 'Mise à jour')}")
            st.progress(float(job.get("progress") or 0), text=job.get("message") or "En cours...")
            st.caption(f"Démarré le {_format_datetime(job.get('started_at'))}")

    if finished:
        with st.expander("Dernières tâches terminées", expanded=False):
            rows = []
            for job in finished:
                rows.append(
                    {
                        "Tâche": job.get("label"),
                        "Statut": "Erreur" if job.get("status") == "error" else "Terminée",
                        "Fin": _format_datetime(job.get("finished_at")),
                        "Message": job.get("error") or job.get("message"),
                    }
                )
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def _launch_import(label: str, league_ids: list[int], seasons: list[int], pause: float = 2.0, max_retries: int = 6):
    job_id = background_jobs.start_manual_import(
        league_ids,
        seasons=seasons,
        pause=pause,
        max_retries=max_retries,
        selected_presets=[label],
    )
    st.success("Mise à jour lancée en arrière-plan. Vous pouvez changer de page.")
    st.caption(f"Job: {job_id}")


def show():
    ui.page_hero(
        "Mise à jour",
        "Suivez les téléchargements, lancez les mises à jour utiles et consultez l’historique récent depuis un seul écran.",
    )

    counts = _summary_counts()
    cols = st.columns(4)
    cols[0].metric("Championnats", counts["leagues"])
    cols[1].metric("Équipes", counts["teams"])
    cols[2].metric("Matchs", counts["matches"])
    cols[3].metric("Classements", counts["standings"])

    _render_jobs()

    ui.section_label("Actions simples")
    config = import_service.get_auto_refresh_config()
    end_season = max(2026, config["end_season"])
    recent_start = max(config["start_season"], end_season - 1)

    with st.container(border=True):
        st.markdown("### Mises à jour recommandées")
        action_cols = st.columns(3)
        if action_cols[0].button("Mettre à jour les saisons récentes", type="primary", width="stretch"):
            seasons = list(range(recent_start, end_season + 1))
            _launch_import("Saisons récentes", list(LEAGUE_PRESETS.values()), seasons)
        if action_cols[1].button("Mettre à jour la saison en cours", width="stretch"):
            _launch_import("Saison en cours", list(LEAGUE_PRESETS.values()), [end_season])
        if action_cols[2].button("Mettre à jour Ligue 1", width="stretch"):
            seasons = list(range(recent_start, end_season + 1))
            _launch_import("Ligue 1", [LEAGUE_PRESETS["Ligue 1"]], seasons)
        st.caption(
            f"Saisons récentes: {season_range(range(recent_start, end_season + 1))}. "
            "Les imports continuent en arrière-plan."
        )

    with st.expander("Import personnalisé", expanded=False):
        selected_labels = st.multiselect(
            "Ligues",
            options=list(LEAGUE_PRESETS.keys()),
            default=list(LEAGUE_PRESETS.keys()),
        )
        col1, col2 = st.columns(2)
        start_season = col1.number_input("Début", min_value=2016, max_value=end_season, value=recent_start, step=1)
        selected_end = col2.number_input("Fin", min_value=2016, max_value=end_season, value=end_season, step=1)
        pause = st.number_input("Pause entre requêtes API", min_value=0.5, max_value=10.0, value=2.0, step=0.5)
        if st.button("Lancer l’import personnalisé", width="stretch"):
            if start_season > selected_end:
                st.error("La saison de début doit être inférieure ou égale à la saison de fin.")
            else:
                _launch_import(
                    "Import personnalisé",
                    [LEAGUE_PRESETS[label] for label in selected_labels],
                    list(range(int(start_season), int(selected_end) + 1)),
                    pause=float(pause),
                )

    ui.section_label("Historique récent")
    logs = _recent_logs()
    if logs.empty:
        st.info("Aucun historique enregistré.")
    else:
        st.dataframe(logs, hide_index=True, width="stretch")


if __name__ == "__main__":
    ui.run_direct_page("Mise à jour", show)
