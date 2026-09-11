import importlib
import os

import pandas as pd
import streamlit as st

from components import ui
from database.database import (
    engine,
    persistence_configuration_error,
    persistence_mode,
    persistence_status,
)
from services import (
    background_jobs,
    full_sync_service,
    import_service,
    sync_registry,
    xg_service,
)
from services.season_format import season_period, season_range


LEAGUE_PRESETS = {
    "Ligue des Champions": 2,
    "Ligue 1": 61,
    "Premier League": 39,
    "La Liga": 140,
    "Serie A": 135,
    "Bundesliga": 78,
}


@st.cache_data(ttl=300, show_spinner=False)
def _summary_counts() -> dict[str, int]:
    try:
        row = pd.read_sql(
            """
            SELECT
                (SELECT COUNT(*) FROM leagues) AS leagues,
                (SELECT COUNT(*) FROM teams) AS teams,
                (SELECT COUNT(*) FROM matches) AS matches,
                (SELECT COUNT(*) FROM standings) AS standings,
                (SELECT COUNT(*) FROM players) AS players,
                (SELECT COUNT(*) FROM fixture_lineups)
                    + (SELECT COUNT(*) FROM projected_lineups) AS lineups,
                (SELECT COUNT(*) FROM match_analysis_snapshots) AS analyses,
                (SELECT COUNT(DISTINCT fixture_id)
                 FROM fixture_team_statistics) AS xg_matches
            """,
            engine,
        ).iloc[0]
        leagues, teams, matches, standings = (
            row["leagues"], row["teams"], row["matches"], row["standings"]
        )
        players, lineups = row["players"], row["lineups"]
        analyses, xg_matches = row["analyses"], row["xg_matches"]
    except Exception:
        leagues = teams = matches = standings = players = lineups = analyses = xg_matches = 0
    return {
        "leagues": int(leagues),
        "teams": int(teams),
        "matches": int(matches),
        "standings": int(standings),
        "players": int(players),
        "lineups": int(lineups),
        "analyses": int(analyses),
        "xg_matches": int(xg_matches),
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
            SELECT event_type, status, started_at, finished_at, reason, details, error
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
                "Message": import_service.update_log_message(
                    row.event_type,
                    row.status,
                    reason=row.reason,
                    error=row.error,
                    details=row.details,
                ),
            }
            for row in logs.itertuples()
        ]
    )


@st.fragment(run_every="1s")
def _render_jobs():
    """Rafraîchit la progression des tâches de fond en temps réel."""
    resume_pending = getattr(
        background_jobs, "resume_pending_full_sync_if_due", None
    ) or getattr(background_jobs, "resume_pending_full_sync", None)
    if callable(resume_pending):
        resume_pending()
    jobs = background_jobs.list_jobs()
    active_statuses = {"running", "waiting_quota"}
    active = [job for job in jobs if job.get("status") in active_statuses]
    finished = [job for job in jobs if job.get("status") not in active_statuses][:5]
    paused = [
        job
        for job in finished
        if job.get("status") == "partial"
        and (job.get("details") or {}).get("quota_reached")
    ][:1]

    ui.section_label("Téléchargements")
    if not active:
        st.info("Aucun téléchargement en cours.")
    for job in active:
        with st.container(border=True):
            st.markdown(f"### {job.get('label', 'Mise à jour')}")
            progress = float(job.get("progress") or 0)
            st.progress(progress, text=ui.progress_bar_text(job))
            st.caption(ui.progress_download_caption(job))
            if job.get("status") == "waiting_quota":
                st.warning(job.get("message"))
            else:
                st.caption("La mise à jour continue automatiquement en arrière-plan.")

    for job in paused:
        with st.container(border=True):
            st.markdown(f"### {job.get('label', 'Mise à jour')} — suspendue")
            progress = float(job.get("progress") or 0)
            st.progress(progress, text=ui.progress_bar_text(job))
            st.caption(ui.progress_download_caption(job))
            st.warning(job.get("message") or "Quota API atteint.")

    if finished:
        with st.expander("Dernières tâches terminées", expanded=False):
            rows = []
            for job in finished:
                details = job.get("details") or {}
                status_label = {
                    "error": "Erreur",
                    "partial": "À reprendre",
                    "done": "Terminée",
                }.get(job.get("status"), "Terminée")
                rows.append(
                    {
                        "Tâche": job.get("label"),
                        "Statut": status_label,
                        "Fin": _format_datetime(job.get("finished_at")),
                        "Appels API": details.get("api_calls", "—"),
                        "Requêtes évitées": details.get(
                            "requests_avoided_at_least",
                            details.get("duplicates_avoided", "—"),
                        ),
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
    st.success("Mise à jour lancée. Vous pouvez continuer à utiliser l’application.")


def _start_prediction_sync() -> str:
    """Lance la synchronisation même après un rechargement partiel Streamlit."""
    starter = getattr(background_jobs, "start_prediction_sync", None)
    sync_predictions = getattr(
        full_sync_service, "sync_all_upcoming_predictions", None
    )
    if not callable(starter) or not callable(sync_predictions):
        importlib.invalidate_caches()
        importlib.reload(full_sync_service)
        importlib.reload(background_jobs)
        starter = getattr(background_jobs, "start_prediction_sync", None)
    if not callable(starter):
        raise RuntimeError(
            "Le service de synchronisation n’est pas encore disponible. "
            "Redémarrez l’application puis réessayez."
        )
    return starter()


def _start_full_sync(*, resumed: bool = False) -> str:
    """Reste compatible avec le service chargé avant un déploiement à chaud."""
    starter = getattr(background_jobs, "start_full_sync", None)
    if not callable(starter):
        raise RuntimeError("Le service de synchronisation globale est indisponible.")
    try:
        return starter(resumed=resumed)
    except TypeError:
        return starter()


def show():
    ui.page_hero(
        "Mise à jour",
        "Lancez une mise à jour et suivez son avancement simplement.",
    )

    config_error = persistence_configuration_error()
    if config_error:
        st.error(f"Configuration Turso incomplète : {config_error}")
    elif persistence_mode() == "sqlite_local":
        st.warning(
            "Base SQLite locale : sur Streamlit Cloud, configurez Turso avant "
            "une synchronisation exhaustive pour conserver les téléchargements."
        )
    else:
        storage_status = persistence_status()
        if storage_status.get("topology") == "local_replica":
            st.success(
                "Réplique SQLite locale active : lectures locales rapides et "
                "sauvegarde synchronisée dans Turso."
            )
            if storage_status.get("pending_push"):
                st.warning(
                    "Des écritures sont conservées localement et attendent leur "
                    "prochaine sauvegarde Turso."
                )
            if storage_status.get("last_error"):
                st.caption(
                    "Dernière synchronisation Turso différée : "
                    f"{storage_status['last_error']}"
                )
        else:
            st.success("Base Turso distante connectée en mode traitement direct.")

    counts = _summary_counts()
    database_kpis = [
            {"label": "Championnats", "value": counts["leagues"], "caption": "Compétitions suivies"},
            {"label": "Équipes", "value": counts["teams"], "caption": "Clubs enregistrés"},
            {"label": "Matchs", "value": counts["matches"], "caption": "Rencontres en base"},
            {"label": "Classements", "value": counts["standings"], "caption": "Positions disponibles"},
            {"label": "Joueurs", "value": counts["players"], "caption": "Profils persistés"},
            {"label": "Compositions", "value": counts["lineups"], "caption": "Onze officiels ou projetés"},
            {"label": "Analyses", "value": counts["analyses"], "caption": "Études conservées"},
            {"label": "Matchs avec xG", "value": counts["xg_matches"], "caption": "Statistiques API stockées"},
        ]
    try:
        ui.kpi_grid(database_kpis, columns=4)
    except TypeError:
        ui.kpi_grid(database_kpis)

    _render_jobs()
    data_job_active = background_jobs.data_job_running()

    ui.section_label("Synchronisation globale")
    with st.container(border=True):
        st.markdown("### Tout mettre à jour")
        st.write(
            "L’application parcourt toutes les saisons configurées et tous les "
            "matchs connus pour conserver les données exploitées : équipes, matchs, "
            "classements, détails, compositions, joueurs, prédictions et xG."
        )
        st.caption(
            "Priorité stricte : les xG manquants sont téléchargés avant toutes "
            "les autres données, avec 1 500 requêtes conservées par défaut pour "
            "maintenir les informations courantes à jour."
        )
        api_key_missing = not (os.getenv("API_FOOTBALL_KEY") or "").strip()
        state_loader = getattr(
            background_jobs, "cached_full_sync_state", None
        ) or getattr(background_jobs, "full_sync_state", None)
        full_state = state_loader() if callable(state_loader) else None
        quota_waiting = bool(
            full_state and full_state.get("status") == "waiting_quota"
        )
        if api_key_missing:
            st.error("Synchronisation indisponible : la clé API_FOOTBALL_KEY est absente. Ajoutez-la dans .env ou les secrets Streamlit.")
        if st.button(
            "↻ Lancer la synchronisation exhaustive",
            type="primary",
            width="stretch",
            disabled=api_key_missing or data_job_active or quota_waiting,
        ):
            _start_full_sync()
            st.success(
                "Synchronisation exhaustive lancée. Chaque donnée reçue est "
                "enregistrée immédiatement dans la base."
            )

        try:
            download_plan = full_sync_service.download_plan()
        except Exception as exc:
            download_plan = None
            st.warning(f"Plan de téléchargement indisponible : {exc}")
        if download_plan:
            st.markdown("#### Plan calculé depuis la base")
            plan_columns = st.columns(4)
            plan_columns[0].metric("Ressources utiles", download_plan["total"])
            plan_columns[1].metric("Déjà en base", download_plan["present"])
            plan_columns[2].metric("À télécharger", download_plan["to_download"])
            plan_columns[3].metric("Différées", download_plan["deferred"])
            plan_rows = pd.DataFrame(download_plan["resources"]).rename(
                columns={
                    "label": "Donnée",
                    "endpoint": "Endpoint",
                    "total": "Utiles",
                    "present": "En base",
                    "to_download": "À télécharger",
                    "deferred": "Différées",
                }
            )
            st.dataframe(
                plan_rows[
                    [
                        "Donnée",
                        "Endpoint",
                        "Utiles",
                        "En base",
                        "À télécharger",
                        "Différées",
                    ]
                ],
                hide_index=True,
                width="stretch",
            )
            st.caption(
                "Ce plan est calculé sans appel API. Chaque élément est contrôlé "
                "une seconde fois juste avant son téléchargement."
            )
        if full_state and full_state.get("status") == "waiting_quota":
            st.warning(full_state.get("message") or "Synchronisation en attente du quota API.")
            st.caption(
                "Cette attente ne bloque plus les autres actions. Aucun appel de "
                "contrôle n’est consommé avant minuit."
            )
            if st.button(
                "Tenter une reprise maintenant",
                key="retry_full_sync_now",
                width="stretch",
                disabled=api_key_missing or data_job_active,
            ):
                _start_full_sync(resumed=True)
                st.success(
                    "Tentative lancée à partir des données conservées, sans attendre "
                    "une confirmation de l’API."
                )
        registry_counts = sync_registry.counts()
        if registry_counts:
            st.caption(
                "Avancement : "
                f"{registry_counts.get('complete', 0)} terminé(s) · "
                f"{registry_counts.get('running', 0)} en cours · "
                f"{registry_counts.get('unavailable', 0)} indisponible(s) · "
                f"{registry_counts.get('error', 0)} en erreur."
            )
        st.caption(
            "En cas de quota atteint, l’avancement reste en base et reprend "
            "automatiquement. Les données déjà complètes ne sont pas retéléchargées."
        )

    ui.section_label("Actions simples")
    config = import_service.get_auto_refresh_config()
    start_season = int(config["start_season"])
    end_season = int(config["end_season"])
    recent_start = max(start_season, end_season - 1)

    with st.container(border=True):
        st.markdown("### Conseils API des matchs à venir")
        coverage = full_sync_service.prediction_coverage()
        st.write(
            f"{coverage['available']} conseil(s) disponible(s) sur "
            f"{coverage['total']} match(s) futur(s) en base "
            f"({coverage['percentage']} %)."
        )
        st.caption(
            "La synchronisation reprend là où elle s’est arrêtée, ignore les "
            "conseils déjà enregistrés et respecte la limite quotidienne de l’API."
        )
        if st.button(
            "Télécharger tous les conseils API",
            type="primary",
            width="stretch",
            disabled=api_key_missing or data_job_active,
        ):
            try:
                _start_prediction_sync()
            except Exception as exc:
                st.error(str(exc))
            else:
                st.success(
                    "Téléchargement lancé en arrière-plan. Les analyses utiliseront "
                    "automatiquement les conseils disponibles."
                )

    with st.container(border=True):
        st.markdown("### xG historiques")
        xg_seasons = list(range(start_season, end_season + 1))
        xg_coverage = xg_service.coverage(
            list(LEAGUE_PRESETS.values()), xg_seasons
        )
        st.write(
            f"{xg_coverage['available']} match(s) avec xG sur "
            f"{xg_coverage['total']} match(s) terminé(s) de toutes les saisons configurées "
            f"({xg_coverage['percentage']} %)."
        )
        if xg_coverage.get("partial"):
            st.caption(
                f"{xg_coverage['partial']} match(s) incomplet(s) : le xG d’une "
                "des deux équipes manque encore."
            )
        st.caption(
            "La différence est calculée avec la base avant chaque appel : un match "
            "possédant déjà les xG des deux équipes ne consomme aucune requête."
        )
        if st.button(
            "Télécharger tous les xG manquants",
            type="primary",
            width="stretch",
            disabled=api_key_missing or data_job_active or quota_waiting,
        ):
            background_jobs.start_xg_sync(
                list(LEAGUE_PRESETS.values()),
                xg_seasons,
                max_matches=None,
            )
            st.success(
                "Téléchargement xG lancé en arrière-plan. La progression est conservée."
            )
        audit = xg_service.recent_audit(25)
        with st.expander("Journal de traçabilité xG", expanded=False):
            if audit.empty:
                st.info("Aucune tentative d’ingestion xG journalisée.")
            else:
                audit = audit.copy()
                audit["match"] = (
                    audit["home_name"].fillna("?")
                    + " – "
                    + audit["away_name"].fillna("?")
                )
                st.dataframe(
                    audit[
                        [
                            "id",
                            "sync_run_id",
                            "fixture_id",
                            "match",
                            "source",
                            "endpoint",
                            "status",
                            "requested_at",
                            "completed_at",
                            "item_count",
                            "has_xg",
                            "payload_sha256",
                            "error",
                        ]
                    ].rename(
                        columns={
                            "id": "Audit",
                            "sync_run_id": "Lot",
                            "fixture_id": "Fixture",
                            "match": "Match",
                            "source": "Source",
                            "endpoint": "Endpoint",
                            "status": "Statut",
                            "requested_at": "Début",
                            "completed_at": "Fin",
                            "item_count": "Éléments",
                            "has_xg": "xG présent",
                            "payload_sha256": "SHA-256",
                            "error": "Erreur",
                        }
                    ),
                    hide_index=True,
                    width="stretch",
                )
                st.caption(
                    "Chaque ligne est une tentative conservée. La réponse brute complète "
                    "est stockée avec son empreinte SHA-256 dans SQLite."
                )

    with st.container(border=True):
        st.markdown("### Mises à jour recommandées")
        if data_job_active:
            st.info(
                "Une mise à jour des données est déjà en cours. Les autres "
                "imports seront disponibles lorsqu’elle sera terminée."
            )
        action_cols = st.columns(3)
        if action_cols[0].button(
            "Mettre à jour les saisons récentes",
            type="primary",
            width="stretch",
            disabled=data_job_active,
        ):
            seasons = list(range(recent_start, end_season + 1))
            _launch_import("Saisons récentes", list(LEAGUE_PRESETS.values()), seasons)
        if action_cols[1].button(
            "Mettre à jour la saison en cours",
            width="stretch",
            disabled=data_job_active,
        ):
            _launch_import("Saison en cours", list(LEAGUE_PRESETS.values()), [end_season])
        if action_cols[2].button(
            "Mettre à jour Ligue 1",
            width="stretch",
            disabled=data_job_active,
        ):
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
        if st.button(
            "Lancer l’import personnalisé",
            width="stretch",
            disabled=data_job_active,
        ):
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
