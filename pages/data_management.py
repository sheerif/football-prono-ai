import datetime as dt
import importlib
import os
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from components import ui
from database.database import engine
from services import (
    background_jobs,
    data_reliability,
    full_sync_service,
    git_backup,
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


@st.cache_data(ttl=15, show_spinner=False)
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


@st.cache_data(ttl=60, show_spinner=False)
def _latest_git_backup_info() -> dict:
    return git_backup.latest_backup_info()


def _database_health() -> dict:
    """Read lightweight live health details for the local SQLite database."""
    with engine.connect() as connection:
        connection.exec_driver_sql("SELECT 1").scalar()
        # SQLite may list several attached databases. Select the main database
        # explicitly instead of requiring PRAGMA database_list to return one row.
        database_rows = connection.exec_driver_sql("PRAGMA database_list").all()
        database_file = next(
            (row[2] for row in database_rows if row[1] == "main"),
            None,
        )
        journal_mode = connection.exec_driver_sql("PRAGMA journal_mode").scalar()
        page_size = int(connection.exec_driver_sql("PRAGMA page_size").scalar())
        page_count = int(connection.exec_driver_sql("PRAGMA page_count").scalar())
        free_pages = int(connection.exec_driver_sql("PRAGMA freelist_count").scalar())
        latest_update = connection.exec_driver_sql(
            "SELECT MAX(finished_at) FROM update_log WHERE finished_at IS NOT NULL"
        ).scalar()
    file_size = os.path.getsize(database_file) if database_file and os.path.isfile(database_file) else None
    return {
        "database_file": database_file,
        "file_size": file_size,
        "journal_mode": str(journal_mode or "inconnu").upper(),
        "page_size": page_size,
        "page_count": page_count,
        "free_pages": free_pages,
        "latest_update": latest_update,
    }


def _format_bytes(value: int | None) -> str:
    if value is None:
        return "Taille indisponible"
    size = float(value)
    for unit in ("o", "Ko", "Mo", "Go"):
        if size < 1024 or unit == "Go":
            return f"{size:.1f} {unit}" if unit != "o" else f"{int(size)} o"
        size /= 1024


@st.fragment(run_every="30s")
def _render_storage_status() -> None:
    """Show live database health and the latest remote Git backup state."""
    st.subheader("État de la base et des sauvegardes")
    database = None
    database_error = None
    try:
        database = _database_health()
    except Exception as exc:
        database_error = str(exc)

    backup = {"status": "not_configured"}
    backup_error = None
    if git_backup.backup_is_configured():
        try:
            backup = _latest_git_backup_info()
        except Exception as exc:
            backup_error = str(exc)

    db_col, backup_col = st.columns(2)
    with db_col, st.container(border=True):
        st.markdown("#### Base SQLite locale")
        if database:
            st.success("🟢 EN LIGNE · connexion vérifiée")
            st.caption(
                f"Fichier : `{database['database_file'] or 'base temporaire'}` · "
                f"{_format_bytes(database['file_size'])} · "
                f"journal {database['journal_mode']}"
            )
            st.caption(
                f"Pages utilisées : {database['page_count'] - database['free_pages']:,} · "
                f"pages libres : {database['free_pages']:,} · "
                f"espace récupérable : "
                f"{_format_bytes(database['free_pages'] * database['page_size'])} · "
                f"dernière activité : {_format_utc_datetime(database['latest_update'])}"
            )
        else:
            st.error("🔴 HORS LIGNE · connexion SQLite impossible")
            st.caption(database_error or "État de la base indisponible.")

    with backup_col, st.container(border=True):
        st.markdown("#### Copie Git distante")
        if backup_error:
            st.warning("🟠 ÉTAT INCONNU · GitHub ne répond pas")
            st.caption(backup_error)
        elif backup.get("status") == "available":
            st.success("🟢 EN LIGNE · sauvegarde trouvée sur GitHub")
            st.caption(
                f"Dépôt : `{backup.get('repository')}` · branche `{backup.get('branch')}`"
            )
            st.caption(
                f"Dernière sauvegarde : {_format_utc_datetime(backup.get('committed_at'))} · "
                f"commit `{str(backup.get('commit') or '')[:8]}`"
            )
        elif backup.get("status") == "not_configured":
            st.info("⚪ SAUVEGARDE AUTOMATIQUE INACTIVE")
            st.caption("Le jeton Git n’est pas accessible à l’application.")
        elif backup.get("status") == "not_found":
            st.warning("🟠 AUCUNE SAUVEGARDE TROUVÉE")
            st.caption(
                f"Dépôt : `{backup.get('repository')}` · branche `{backup.get('branch')}`"
            )
        else:
            st.error("🔴 SAUVEGARDE INCOMPLÈTE · fichier SQLite absent")
            st.caption(
                f"Dépôt : `{backup.get('repository')}` · branche `{backup.get('branch')}`"
            )


def _format_informational_timestamp(value, *, label="Message généré") -> str:
    """Format a timestamp locally so the page does not depend on a stale UI module."""
    if not value:
        return ""
    try:
        timestamp = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=dt.timezone.utc)
        timestamp = timestamp.astimezone(ZoneInfo("Europe/Paris"))
    except (TypeError, ValueError):
        return ""
    return f"{label} le {timestamp:%d/%m/%Y à %H:%M} (heure de Paris)."


def _quota_metadata_for_job(job: dict, metadata: dict | None = None) -> dict:
    """Use the job canonical progress for both the bar and quota notice."""
    result = dict(metadata or {})
    progress = job.get("progress")
    if progress is not None:
        try:
            result["progress"] = max(0.0, min(100.0, float(progress) * 100.0))
        except (TypeError, ValueError):
            pass
    return result

def _format_datetime(value):
    timestamp = pd.to_datetime(value, errors="coerce")
    if pd.isna(timestamp):
        return value or "-"
    return timestamp.strftime("%d/%m/%Y %H:%M")


def _format_utc_datetime(value):
    timestamp = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(timestamp):
        return value or "-"
    return timestamp.strftime("%d/%m/%Y à %H:%M UTC")


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


@st.fragment(run_every="2s")
def _render_api_quota_status():
    """Affiche le quota et son éventuelle suspension depuis une seule source."""
    if not (os.getenv("API_FOOTBALL_KEY") or "").strip():
        st.caption("Quota API indisponible : clé API absente.")
        return

    quota = background_jobs.api_quota_status()
    _render_quota_counters(quota)

    error = str(quota.get("error") or "").casefold()
    daily_exhausted = (
        quota.get("verified") and int(quota.get("remaining") or 0) <= 0
    ) or any(token in error for token in ("day", "daily", "journalier"))
    minute_exhausted = (
        quota.get("minute_remaining") is not None
        and int(quota["minute_remaining"]) <= 0
    ) or any(token in error for token in ("minute", "per minute", "rate limit"))

    full_state = background_jobs.cached_full_sync_state()
    waiting = bool(full_state and full_state.get("status") == "waiting_quota")
    if waiting:
        metadata = full_state.get("metadata") or {}
        st.warning(
            _quota_wait_message(
                metadata,
                automatic=True,
                fallback_message=full_state.get("message") or "",
            )
        )
    elif quota.get("api_refused") or daily_exhausted or minute_exhausted:
        if minute_exhausted and not daily_exhausted:
            label = "Limite API par minute atteinte."
        elif daily_exhausted:
            label = "Quota API quotidien épuisé."
        else:
            label = "Quota API non confirmé : l’API a refusé le contrôle."
        timestamp = _format_informational_timestamp(
            quota.get("checked_at"), label="Dernière vérification"
        )
        st.warning(
            f"{label} Aucun nouvel appel ne sera lancé tant que la limite "
            "n’est pas levée."
            + (f" {timestamp}" if timestamp else "")
        )
    elif not quota.get("verified"):
        st.info(
            "Quota API non vérifiable. Les compteurs restent indisponibles "
            "et chaque téléchargement sera contrôlé avant son lancement."
        )


def _render_quota_counters(quota: dict) -> None:
    """Show daily and per-minute API call budgets without trusting refusals."""
    st.markdown("#### Appels API disponibles")
    verified = bool(quota.get("verified"))
    refused = bool(quota.get("api_refused"))
    current = quota.get("current")
    daily_limit = quota.get("limit")
    remaining = quota.get("remaining")
    minute_remaining = quota.get("minute_remaining")
    minute_limit = quota.get("minute_limit")

    if verified and daily_limit and current is not None:
        used = max(0, int(current))
        limit = max(1, int(daily_limit))
        daily_remaining = max(0, int(remaining or 0))
        percent_used = min(100, round(100 * used / limit))
        progress_text = (
            f"{percent_used} % utilisés · {used:,} / {limit:,} appels · "
            f"{daily_remaining:,} annoncés restants"
        )
        st.progress(min(1.0, used / limit), text=progress_text)
        daily_label = (
            "Appels annoncés restants"
            if refused
            else "Appels disponibles aujourd’hui"
        )
        daily_value = f"{daily_remaining:,}"
        used_value = f"{used:,} / {limit:,}"
    else:
        daily_label = "Appels disponibles aujourd’hui"
        daily_value = "Indisponible"
        used_value = "Indisponible"

    if minute_remaining is not None:
        minute_value = (
            f"{int(minute_remaining):,} / {int(minute_limit):,}"
            if minute_limit
            else f"{int(minute_remaining):,}"
        )
    else:
        minute_value = "Indisponible"

    daily_col, used_col, minute_col = st.columns(3)
    daily_col.metric(daily_label, daily_value)
    used_col.metric("Utilisés aujourd’hui", used_value)
    minute_col.metric("Restants cette minute", minute_value)

    checked_at = quota.get("checked_at")
    if checked_at:
        checked_label = _format_utc_datetime(checked_at)
        freshness_note = (
            " · compteur non confirmé, l’API a refusé le contrôle"
            if refused
            else ""
        )
        st.caption(f"Dernière vérification : {checked_label}{freshness_note}.")
    if refused and verified and int(remaining or 0) > 0:
        st.caption(
            "Le compteur restant vient des en-têtes HTTP et contredit le refus "
            "explicite de l’API; les appels affichés ne sont donc pas considérés "
            "comme disponibles."
        )


def _quota_eta(*, quota: dict | None = None, daily: bool) -> dt.datetime:
    now = dt.datetime.now(dt.timezone.utc)
    if daily:
        return (now + dt.timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    quota = quota or {}
    retry_seconds = _safe_positive_seconds(quota.get("retry_after"), default=90)
    checked = pd.to_datetime(quota.get("checked_at"), errors="coerce", utc=True)
    checked_at = checked.to_pydatetime() if not pd.isna(checked) else now
    return checked_at + dt.timedelta(seconds=retry_seconds)


def _safe_positive_seconds(value, *, default: int) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


def _quota_reset_label(quota: dict | None = None, *, daily: bool) -> str:
    eta = _quota_eta(quota=quota, daily=daily)
    paris_time = eta.astimezone(ZoneInfo("Europe/Paris"))
    remaining = max(0, int((eta - dt.datetime.now(dt.timezone.utc)).total_seconds()))
    hours, remainder = divmod(remaining, 3600)
    minutes = remainder // 60
    seconds = remainder % 60
    countdown = (
        f"dans {hours} h {minutes:02d}"
        if hours
        else f"dans {minutes} min" if minutes
        else f"dans {seconds} s"
    )
    reset_name = "Renouvellement quotidien estimé" if daily else "Nouvelle tentative estimée"
    return (
        f"{reset_name} : {paris_time:%d/%m/%Y à %H:%M} (heure de Paris, "
        f"{eta:%H:%M} UTC), {countdown}."
    )


def _quota_wait_message(
    metadata: dict, *, automatic: bool, fallback_message: str = ""
) -> str:
    return ui.quota_wait_message(
        metadata,
        automatic=automatic,
        fallback_message=fallback_message,
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
                st.caption("En attente du quota API · progression conservée.")
            else:
                st.caption("Mise à jour active en arrière-plan.")

    for job in paused:
        with st.container(border=True):
            st.markdown(f"### {job.get('label', 'Mise à jour')} — suspendue")
            progress = float(job.get("progress") or 0)
            st.progress(progress, text=ui.progress_bar_text(job))
            st.caption(ui.progress_download_caption(job))
            details = job.get("details") or {}
            st.caption("Mise à jour interrompue · progression conservée pour reprise.")

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
                        "Sauvegarde Git": {
                            "uploaded": "Effectuée",
                            "unchanged": "Aucun changement",
                            "skipped_daily_limit": "Déjà faite aujourd’hui",
                            "pending": "En cours",
                            "not_configured": "Non configurée",
                            "error": "Erreur",
                        }.get(job.get("backup_status"), "—"),
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


@st.fragment(run_every="15s")
def _render_database_kpis() -> None:
    """Rafraîchit les volumes SQLite sans attendre un rechargement complet."""
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


@st.fragment(run_every="15s")
def _render_reliability_engine() -> None:
    """Expose the central reliability engine without recalculating business KPIs."""
    st.subheader("Moteur de fiabilité des données")
    try:
        snapshot = data_reliability.get_reliability_snapshot()
    except Exception as exc:
        st.error(f"Contrôle de fiabilité indisponible ({type(exc).__name__}).")
        return

    labels = {
        "healthy": "FIABLE",
        "warning": "À SURVEILLER",
        "critical": "CRITIQUE",
        "unknown": "INCONNU",
    }
    overall = snapshot.get("overall_status", "unknown")
    if overall == "healthy":
        st.success(f"État global : {labels[overall]}")
    elif overall == "critical":
        st.error(f"État global : {labels[overall]}")
    else:
        st.warning(f"État global : {labels.get(overall, labels['unknown'])}")

    summary = snapshot.get("summary") or {}
    cols = st.columns(4)
    for col, status in zip(cols, ("healthy", "warning", "critical", "unknown")):
        col.metric(labels[status].capitalize(), int(summary.get(status, 0)))

    table_rows = []
    for item in snapshot.get("tables") or []:
        age = item.get("age_seconds")
        age_label = (
            f"{age / 3600:.1f} h" if age is not None
            else "Non vérifiable"
        )
        table_rows.append({
            "Table": item.get("name"),
            "Lignes": item.get("count") if item.get("count") is not None else "Inconnu",
            "État": labels.get(item.get("status"), "INCONNU"),
            "Dernière donnée": _format_utc_datetime(item.get("last_updated_at")),
            "Âge": age_label,
            "Source horodatage": item.get("timestamp_source") or "Aucune",
            "Diagnostic": item.get("message"),
        })
    if table_rows:
        st.dataframe(pd.DataFrame(table_rows), use_container_width=True, hide_index=True)

    archive = snapshot.get("api_archive") or {}
    archive_col, update_col = st.columns(2)
    with archive_col:
        st.markdown("**Intégrité de l’archive API**")
        st.write(labels.get(archive.get("status"), "INCONNU"))
        if archive.get("checked") is not None:
            st.caption(
                f"Échantillon : {archive.get('checked')} · "
                f"valides : {archive.get('valid')} · "
                f"invalides : {archive.get('invalid')}"
            )
        st.caption(archive.get("message") or "Contrôle non disponible.")
    with update_col:
        st.markdown("**Dernière synchronisation terminée**")
        st.write(_format_utc_datetime(snapshot.get("latest_update")))
        sync_states = snapshot.get("sync_states")
        if sync_states is None:
            st.caption("État des ressources de synchronisation inconnu.")
        else:
            st.caption(" · ".join(f"{key} : {value}" for key, value in sorted(sync_states.items())) or "Aucune ressource suivie.")
    st.caption(
        "Instantané généré à " + _format_utc_datetime(snapshot.get("generated_at"))
        + ". Une valeur inconnue n’est jamais assimilée à zéro."
    )


def show():
    ui.page_hero(
        "Mise à jour",
        "Lancez une mise à jour et suivez son avancement simplement.",
    )

    if git_backup.backup_is_configured():
        st.caption(
            "Chaque sauvegarde applique d’abord la maintenance SQLite, vérifie "
            "l’intégrité de l’instantané et de l’archive GZIP, puis contrôle le "
            "fichier distant après son envoi sur Git."
        )
        if st.button("Créer une sauvegarde maintenant", key="create_git_database_backup"):
            try:
                with st.spinner(
                    "Maintenance SQLite, contrôle d’intégrité, compression "
                    "et vérification de la sauvegarde distante…"
                ):
                    backup = git_backup.backup_to_github(force=True)
                if backup["status"] == "uploaded":
                    _latest_git_backup_info.clear()
                    st.success(
                        "Sauvegarde Git vérifiée : "
                        f"{backup['repository']} · branche {backup['branch']} · "
                        f"commit {backup['commit'][:8]}"
                    )
                    st.caption(
                        "Maintenance SQLite confirmée · intégrité SQLite vérifiée · "
                        "GZIP vérifié · fichier distant vérifié."
                    )
                else:
                    st.info("La copie Git est déjà à jour : aucune différence à enregistrer.")
            except Exception as exc:
                st.error(f"Échec de la sauvegarde Git : {exc}")
    else:
        st.caption(
            "L’optimisation SQLite et la compaction des pages inutilisées "
            "s’exécutent après chaque synchronisation ou import."
        )

    _render_storage_status()
    _render_reliability_engine()

    _render_database_kpis()

    _render_jobs()
    data_job_active = background_jobs.data_job_running()

    _render_api_quota_status()

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
            metadata = full_state.get("metadata") or {}
            st.caption(
                "En pause à cause du quota API. La progression est conservée "
                "et la reprise automatique est planifiée."
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
