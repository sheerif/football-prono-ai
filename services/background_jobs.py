import datetime
import math
import os
import threading
import time
import traceback
import uuid

from services import import_service, sync_registry


_lock = threading.RLock()
_jobs: dict[str, dict] = {}
_startup_started = False
_full_progress_cache: tuple[datetime.datetime, dict] | None = None
_quota_status_cache: tuple[datetime.datetime, dict] | None = None
_full_state_read_cache: tuple[float, dict | None] | None = None
FULL_SYNC_CONTROL_KEY = "full-sync:exhaustive"
ACTIVE_JOB_STATUSES = {"running", "waiting_quota"}
DATA_JOB_KINDS = {
    "manual_import",
    "full_sync",
    "prediction_sync",
    "xg_sync",
    "startup_updates",
}


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None).isoformat()


def _set_job(job_id: str, **updates):
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return
        job.update(updates)


def _create_job(kind: str, label: str, details: dict | None = None) -> str:
    job_id = str(uuid.uuid4())
    with _lock:
        _jobs[job_id] = {
            "id": job_id,
            "kind": kind,
            "label": label,
            "status": "running",
            "progress": 0.0,
            "progress_current": 0,
            "progress_total": 0,
            "progress_label": "Préparation...",
            "message": "0 % — Préparation...",
            "details": details or {},
            "started_at": _now(),
            "finished_at": None,
            "error": None,
        }
    return job_id


def _create_unique_data_job(
    kind: str,
    label: str,
    details: dict | None = None,
) -> tuple[str, bool]:
    """Atomically reuse the active data job or create a new one."""
    with _lock:
        existing = next(
            (
                job["id"]
                for job in _jobs.values()
                if job.get("kind") in DATA_JOB_KINDS
                and job.get("status") in ACTIVE_JOB_STATUSES
            ),
            None,
        )
        if existing:
            return existing, False
        return _create_job(kind, label, details), True


def list_jobs() -> list[dict]:
    with _lock:
        return sorted(
            [job.copy() for job in _jobs.values()],
            key=lambda job: job.get("started_at") or "",
            reverse=True,
        )


def _job_progress_snapshot(job_id: str) -> dict:
    with _lock:
        job = (_jobs.get(job_id) or {}).copy()
    return {
        "progress": float(job.get("progress") or 0),
        "progress_current": int(job.get("progress_current") or 0),
        "progress_total": int(job.get("progress_total") or 0),
        "progress_label": str(job.get("progress_label") or "").strip(),
    }


def active_jobs() -> list[dict]:
    return [
        job for job in list_jobs() if job.get("status") in ACTIVE_JOB_STATUSES
    ]


def data_job_running() -> bool:
    return any(job.get("kind") in DATA_JOB_KINDS for job in active_jobs())


def _is_minute_quota(result: dict | None = None) -> bool:
    error_text = " ".join((result or {}).get("errors") or []).casefold()
    return any(
        token in error_text
        for token in ("minute", "rate limit", "limite de requêtes")
    )


def _full_sync_retry_seconds(result: dict | None = None, now=None) -> int:
    """Calcule une reprise adaptée au quota minute ou au quota journalier."""
    configured = os.getenv("FULL_SYNC_QUOTA_RETRY_SECONDS")
    if configured is not None:
        try:
            return max(60, int(configured))
        except (TypeError, ValueError):
            pass

    if _is_minute_quota(result):
        return 90

    # Le forfait utilisé par l'application est renouvelé chaque jour à minuit
    # UTC. La reprise ne dépend d'aucun signal envoyé par le fournisseur.
    current = now or datetime.datetime.now(datetime.UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=datetime.UTC)
    else:
        current = current.astimezone(datetime.UTC)
    next_midnight = (current + datetime.timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return max(60, math.ceil((next_midnight - current).total_seconds()))


def full_sync_state() -> dict | None:
    """Expose l'état durable de la synchronisation exhaustive."""
    try:
        state = sync_registry.get(FULL_SYNC_CONTROL_KEY)
    except Exception:
        return None
    if state and state.get("status") == "waiting_quota":
        metadata = dict(state.get("metadata") or {})
        message = str(state.get("message") or "")
        retry_at_raw = metadata.get("next_retry_at")
        if (
            "quota api journalier atteint" in message.casefold()
            and not metadata.get("budget_reserved")
            and retry_at_raw
        ):
            try:
                retry_at = datetime.datetime.fromisoformat(retry_at_raw).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                metadata["next_retry_at"] = retry_at.isoformat()
            except (TypeError, ValueError):
                pass
            state = {
                **state,
                "message": (
                    "Quota API journalier atteint : données conservées. "
                    "Reprise automatique à minuit UTC."
                ),
                "metadata": metadata,
            }
        durable = _durable_full_progress_snapshot()
        if durable and float(durable.get("progress") or 0) > float(
            metadata.get("progress") or 0
        ):
            metadata.update(durable)
            state = {**state, "metadata": metadata}
    return state


def cached_full_sync_state(ttl_seconds: int = 30) -> dict | None:
    """Share the durable control read between Streamlit's 1-second fragments."""
    global _full_state_read_cache
    now = time.monotonic()
    with _lock:
        cached = _full_state_read_cache
        if cached and now - cached[0] < max(1, int(ttl_seconds)):
            return dict(cached[1]) if cached[1] is not None else None
    state = full_sync_state()
    with _lock:
        _full_state_read_cache = (now, dict(state) if state is not None else None)
    return dict(state) if state is not None else None


def _durable_full_progress_snapshot(*, force: bool = False) -> dict:
    global _full_progress_cache
    now = datetime.datetime.now(datetime.UTC)
    if (
        not force
        and _full_progress_cache
        and (now - _full_progress_cache[0]).total_seconds() < 60
    ):
        return dict(_full_progress_cache[1])
    try:
        from services import full_sync_service

        snapshot = full_sync_service.overall_progress_snapshot()
    except Exception:
        snapshot = {}
    _full_progress_cache = (now, dict(snapshot))
    return snapshot


def _best_progress_snapshot(job_id: str) -> dict:
    current = _job_progress_snapshot(job_id)
    durable = _durable_full_progress_snapshot(force=True)
    if float(durable.get("progress") or 0) > float(current.get("progress") or 0):
        return durable
    return current


def api_quota_status(*, force: bool = False) -> dict:
    """Lit uniquement les compteurs du endpoint /status, sans exposer le compte."""
    global _quota_status_cache
    now = datetime.datetime.now(datetime.UTC)
    if (
        not force
        and _quota_status_cache
        and (now - _quota_status_cache[0]).total_seconds() < 60
    ):
        return dict(_quota_status_cache[1])
    try:
        payload = import_service.client.get_status()
        response = payload.get("response") or {}
        if isinstance(response, list):
            response = response[0] if response else {}
        requests = response.get("requests") or {}
        current = int(requests.get("current") or 0)
        daily_limit = int(requests.get("limit_day") or 0)
        result = {
            "verified": daily_limit > 0,
            "current": current,
            "limit": daily_limit,
            "remaining": max(0, daily_limit - current),
            "available": daily_limit > current,
            "checked_at": now.replace(tzinfo=None).isoformat(),
        }
    except Exception as exc:
        result = {
            "verified": False,
            "available": False,
            "error": str(exc),
            "checked_at": now.replace(tzinfo=None).isoformat(),
        }
    _quota_status_cache = (now, dict(result))
    return result


def _progress(job_id: str, current: int, total: int, label: str):
    ratio = min(1.0, current / max(1, total))
    percent = int(round(ratio * 100))
    _set_job(
        job_id,
        progress=ratio,
        progress_current=max(0, int(current)),
        progress_total=max(1, int(total)),
        progress_label=str(label),
        message=f"{percent} % — {label}",
    )


def _phase_progress(
    job_id: str,
    current: int,
    total: int,
    start: float,
    end: float,
    label: str,
):
    """Projette la progression d'une sous-tâche dans sa portion du total."""
    phase_ratio = min(1.0, max(0.0, current / max(1, total)))
    ratio = min(1.0, max(0.0, start + (end - start) * phase_ratio))
    percent = int(round(ratio * 100))
    _set_job(
        job_id,
        progress=ratio,
        progress_current=max(0, int(current)),
        progress_total=max(1, int(total)),
        progress_label=str(label),
        message=f"{percent} % — {label}",
    )


def _result_metrics(result: dict | None) -> dict:
    result = result or {}
    return {
        "downloaded": int(result.get("downloaded") or 0),
        "skipped": int(
            result.get("requests_avoided_at_least", result.get("skipped") or 0)
            or 0
        ),
        "api_calls": int(result.get("api_calls") or 0),
    }


def start_manual_import(
    league_ids: list[int],
    seasons: list[int],
    pause: float,
    max_retries: int,
    selected_presets=None,
) -> str:
    job_id, created = _create_unique_data_job(
        "manual_import",
        "Import manuel",
        {
            "league_ids": league_ids,
            "seasons": seasons,
            "pause": pause,
            "max_retries": max_retries,
            "selected_presets": selected_presets or [],
        },
    )
    if not created:
        return job_id

    def run():
        started_at = _now()
        try:
            _progress(job_id, 0, 1, "Initialisation de la base...")
            import_service.init_db()
            import_service.import_leagues_cautious(
                league_ids,
                seasons=seasons,
                pause=float(pause),
                max_retries=int(max_retries),
                progress_callback=lambda current, total, label: _progress(job_id, current, total, label),
            )
            _set_job(job_id, status="done", progress=1.0, message="Import terminé", finished_at=_now())
            import_service.record_update_log(
                event_type="import_manuel",
                status="effectuée",
                started_at=started_at,
                reason="Import manuel terminé en arrière-plan.",
                leagues=league_ids,
                seasons=seasons,
                details={
                    "selected_presets": selected_presets or [],
                    "pause": float(pause),
                    "max_retries": int(max_retries),
                    "background": True,
                },
            )
        except Exception as exc:
            _set_job(
                job_id,
                status="error",
                error=str(exc),
                message="Erreur pendant l’import",
                finished_at=_now(),
                traceback=traceback.format_exc(),
            )
            import_service.record_update_log(
                event_type="import_manuel",
                status="erreur",
                started_at=started_at,
                reason="Erreur pendant l’import manuel en arrière-plan.",
                leagues=league_ids,
                seasons=seasons,
                details={
                    "selected_presets": selected_presets or [],
                    "pause": float(pause),
                    "max_retries": int(max_retries),
                    "background": True,
                },
                error=str(exc),
            )

    threading.Thread(target=run, name=f"football-prono-import-{job_id[:8]}", daemon=True).start()
    return job_id


def start_prediction_sync(days: int | None = None) -> str:
    """Synchronise en arrière-plan tous les conseils API des matchs futurs."""
    job_id, created = _create_unique_data_job(
        "prediction_sync",
        "Conseils API",
        {"days": days, "incremental": True},
    )
    if not created:
        return job_id

    def run():
        from services import full_sync_service

        started_at = _now()
        try:
            result = full_sync_service.sync_all_upcoming_predictions(
                days=days,
                progress_callback=lambda current, total, label: _progress(
                    job_id, current, total, label
                ),
            )
            quota_reached = bool(result.get("quota_reached"))
            message = (
                "Limite API atteinte : progression conservée, relancez plus tard."
                if quota_reached
                else "Conseils API synchronisés"
            )
            _set_job(
                job_id,
                status="partial" if quota_reached else "done",
                message=message,
                finished_at=_now(),
                details=result,
                **_result_metrics(result),
                **({"progress": 1.0} if not quota_reached else {}),
            )
            import_service.record_update_log(
                event_type="synchronisation_conseils_api",
                status="partielle" if quota_reached else "effectuée",
                started_at=started_at,
                reason=message,
                details={**result, "background": True},
            )
        except Exception as exc:
            _set_job(
                job_id,
                status="error",
                error=str(exc),
                message="Erreur pendant la synchronisation des conseils API",
                finished_at=_now(),
                traceback=traceback.format_exc(),
            )

    threading.Thread(
        target=run,
        name=f"football-prono-predictions-{job_id[:8]}",
        daemon=True,
    ).start()
    return job_id


def start_xg_sync(
    league_ids: list[int] | None = None,
    seasons: list[int] | None = None,
    max_matches: int | None = 100,
) -> str:
    """Complète en arrière-plan l'historique xG sans dépasser le lot demandé."""
    job_id, created = _create_unique_data_job(
        "xg_sync",
        "xG historiques",
        {
            "league_ids": league_ids or [],
            "seasons": seasons or [],
            "max_matches": max_matches,
            "incremental": True,
        },
    )
    if not created:
        return job_id

    def run():
        from services import full_sync_service

        started_at = _now()
        try:
            result = full_sync_service.sync_historical_xg(
                league_ids=league_ids,
                seasons=seasons,
                max_matches=max_matches,
                progress_callback=lambda current, total, label: _progress(
                    job_id, current, total, label
                ),
            )
            quota_reached = bool(result.get("quota_reached"))
            message = (
                "Limite API atteinte : les xG enregistrés sont conservés."
                if quota_reached
                else "xG historiques synchronisés"
            )
            _set_job(
                job_id,
                status="partial" if quota_reached else "done",
                message=message,
                finished_at=_now(),
                details=result,
                **_result_metrics(result),
                **({"progress": 1.0} if not quota_reached else {}),
            )
            import_service.record_update_log(
                event_type="synchronisation_xg",
                status="partielle" if quota_reached else "effectuée",
                started_at=started_at,
                reason=message,
                leagues=league_ids,
                seasons=seasons,
                details={**result, "background": True, "incremental": True},
            )
        except Exception as exc:
            _set_job(
                job_id,
                status="error",
                error=str(exc),
                message="Erreur pendant la synchronisation des xG",
                finished_at=_now(),
                traceback=traceback.format_exc(),
            )
            import_service.record_update_log(
                event_type="synchronisation_xg",
                status="erreur",
                started_at=started_at,
                reason="Erreur pendant la synchronisation des xG.",
                leagues=league_ids,
                seasons=seasons,
                details={"background": True, "incremental": True},
                error=str(exc),
            )

    threading.Thread(
        target=run,
        name=f"football-prono-xg-{job_id[:8]}",
        daemon=True,
    ).start()
    return job_id


def start_full_sync(*, resumed: bool = False) -> str:
    """Lance la synchronisation exhaustive avec reprise automatique sur quota."""
    job_id, created = _create_unique_data_job(
        "full_sync",
        "Synchronisation exhaustive",
        {"incremental": True, "persistent": True, "resumed": resumed},
    )
    if not created:
        return job_id

    def run():
        from services import full_sync_service

        started_at = _now()
        previous_state = full_sync_state() or {}
        attempt = int((previous_state.get("metadata") or {}).get("attempt") or 0) + 1
        try:
            _set_job(
                job_id,
                status="running",
                message=f"Synchronisation exhaustive · passage {attempt}",
                error=None,
            )
            sync_registry.mark(
                FULL_SYNC_CONTROL_KEY,
                "full_sync_control",
                "running",
                message=f"Passage {attempt} en cours",
                metadata={
                    "persistent": True,
                    "attempt": attempt,
                    "job_id": job_id,
                    "started_at": started_at,
                },
            )
            result = full_sync_service.run_full_sync(
                progress_callback=lambda current, total, label: _progress(
                    job_id, current, total, label
                )
            )
            quota_reached = bool(result.get("quota_reached"))
            if quota_reached:
                now = datetime.datetime.now(datetime.UTC)
                retry_seconds = _full_sync_retry_seconds(result, now=now)
                retry_at = (now + datetime.timedelta(seconds=retry_seconds)).replace(
                    tzinfo=None
                )
                budget_reserved = bool(result.get("budget_reserved"))
                daily_reserve = int(result.get("daily_reserve") or 0)
                quota_kind = "minute" if _is_minute_quota(result) else "journalier"
                if budget_reserved:
                    waiting_message = (
                        f"Réserve quotidienne protégée : {daily_reserve} "
                        "requêtes conservées pour les prédictions et mises à jour courantes. "
                        "La synchronisation exhaustive reprendra à minuit UTC."
                    )
                elif quota_kind == "minute":
                    waiting_message = (
                        "Quota API minute atteint : données conservées. "
                        "Nouvelle tentative automatique dans 90 secondes."
                    )
                else:
                    waiting_message = (
                        "Quota API journalier atteint : données conservées. "
                        "Reprise automatique à minuit UTC."
                    )
                metadata = {
                    "persistent": True,
                    "attempt": attempt,
                    "job_id": job_id,
                    "started_at": started_at,
                    "next_retry_at": retry_at.isoformat(),
                    "checkpoint": result.get("checkpoint"),
                    "budget_reserved": budget_reserved,
                    "daily_reserve": daily_reserve,
                    **_best_progress_snapshot(job_id),
                    **_result_metrics(result),
                }
                sync_registry.mark(
                    FULL_SYNC_CONTROL_KEY,
                    "full_sync_control",
                    "waiting_quota",
                    message=waiting_message,
                    metadata=metadata,
                )
                _set_job(
                    job_id,
                    status="partial",
                    message=waiting_message,
                    finished_at=_now(),
                    details={**result, **metadata},
                    **_result_metrics(result),
                )
                import_service.record_update_log(
                    event_type="synchronisation_globale",
                    status="en_attente_quota",
                    started_at=started_at,
                    reason=waiting_message,
                    details={**result, **metadata, "background": True},
                )
                return

            has_partial_data = bool(result.get("partial")) or bool(
                result.get("errors")
            )
            message = (
                "Synchronisation terminée avec des données indisponibles signalées."
                if has_partial_data
                else "Synchronisation exhaustive terminée"
            )
            _set_job(
                job_id,
                status="partial" if has_partial_data else "done",
                progress=1.0,
                message=message,
                finished_at=_now(),
                details=result,
                **_result_metrics(result),
            )
            sync_registry.mark(
                FULL_SYNC_CONTROL_KEY,
                "full_sync_control",
                "partial" if has_partial_data else "complete",
                message=message,
                metadata={
                    "persistent": True,
                    "attempt": attempt,
                    "job_id": job_id,
                    "started_at": started_at,
                    "finished_at": _now(),
                },
            )
            import_service.record_update_log(
                event_type="synchronisation_globale",
                status="partielle" if has_partial_data else "effectuée",
                started_at=started_at,
                reason=message,
                details={
                    **result,
                    "background": True,
                    "incremental": True,
                    "persistent": True,
                    "attempt": attempt,
                },
            )
        except Exception as exc:
            _set_job(
                job_id,
                status="error",
                error=str(exc),
                message="Erreur pendant la synchronisation globale",
                finished_at=_now(),
                traceback=traceback.format_exc(),
            )
            sync_registry.mark(
                FULL_SYNC_CONTROL_KEY,
                "full_sync_control",
                "error",
                message=str(exc),
                metadata={
                    "persistent": True,
                    "attempt": attempt,
                    "job_id": job_id,
                    "started_at": started_at,
                    "finished_at": _now(),
                },
            )
            import_service.record_update_log(
                event_type="synchronisation_globale",
                status="erreur",
                started_at=started_at,
                reason="Erreur pendant la synchronisation globale.",
                details={"background": True, "incremental": True},
                error=str(exc),
            )

    threading.Thread(
        target=run,
        name=f"football-prono-full-sync-{job_id[:8]}",
        daemon=True,
    ).start()
    return job_id


def resume_pending_full_sync() -> str | None:
    """Relance après redémarrage un traitement interrompu ou un quota expiré."""
    if data_job_running():
        return None
    state = full_sync_state()
    if not state or state.get("status") not in {"running", "waiting_quota"}:
        return None
    metadata = state.get("metadata") or {}
    retry_at_raw = metadata.get("next_retry_at")
    if state.get("status") == "waiting_quota" and retry_at_raw:
        try:
            if datetime.datetime.fromisoformat(retry_at_raw) > datetime.datetime.now(
                datetime.UTC
            ).replace(tzinfo=None):
                return None
        except (TypeError, ValueError):
            pass
        # Ne pas dépendre de /status : certains forfaits ne publient aucun
        # signal de renouvellement exploitable. La première requête métier
        # confirme directement la disponibilité réelle.
    return start_full_sync(resumed=True)


def resume_pending_full_sync_if_due() -> str | None:
    """Cloud-friendly resume check, backed by the shared durable-state cache."""
    if data_job_running():
        return None
    state = cached_full_sync_state()
    if not state or state.get("status") not in {"running", "waiting_quota"}:
        return None
    metadata = state.get("metadata") or {}
    retry_at_raw = metadata.get("next_retry_at")
    if state.get("status") == "waiting_quota" and retry_at_raw:
        try:
            if datetime.datetime.fromisoformat(retry_at_raw) > datetime.datetime.now(
                datetime.UTC
            ).replace(tzinfo=None):
                return None
        except (TypeError, ValueError):
            pass
    return start_full_sync(resumed=True)


def start_startup_updates_once(connection_log_id: int | None = None) -> str | None:
    global _startup_started
    # The daily GitHub workflow is the single automatic writer for Turso.  A
    # Community Cloud restart must not launch the same memory/API-heavy import
    # again.  It can still be explicitly enabled when desired.
    from database.database import persistence_topology

    startup_setting = os.getenv("STREAMLIT_STARTUP_UPDATES")
    if startup_setting is None:
        startup_enabled = persistence_topology() != "remote_direct"
    else:
        startup_enabled = startup_setting.strip().lower() in {
            "1", "true", "yes", "oui"
        }
    if not startup_enabled:
        return None
    with _lock:
        if _startup_started:
            return None
        _startup_started = True

    job_id = _create_job("startup_updates", "Mises à jour de démarrage")

    def run():
        try:
            started_at = _now()
            _progress(job_id, 0, 100, "Initialisation de la base...")
            import_service.init_db()

            _progress(job_id, 2, 100, "Mise à jour des championnats en cours...")
            current_started_at = _now()
            current_result = import_service.refresh_current_competitions_on_connection(
                progress_callback=lambda current, total, label: _phase_progress(
                    job_id,
                    current,
                    total,
                    0.02,
                    0.48,
                    label,
                )
            )
            import_service.record_update_result("championnats_en_cours", current_started_at, current_result)
            if current_result.get("ran") and connection_log_id:
                import_service.mark_connection_current_refreshed(connection_log_id)

            _progress(job_id, 50, 100, "Synchronisation historique si nécessaire...")
            auto_started_at = _now()
            refreshed_current_seasons = {
                int(item["season"])
                for item in (current_result.get("refreshed") or [])
                if item.get("season") is not None
            }
            auto_result = import_service.auto_refresh_if_due(
                skip_force_refresh_seasons=refreshed_current_seasons,
                progress_callback=lambda current, total, label: _phase_progress(
                    job_id,
                    current,
                    total,
                    0.50,
                    0.98,
                    label,
                )
            )
            import_service.record_update_result("historique_auto", auto_started_at, auto_result)

            _progress(job_id, 100, 100, "Mises à jour de démarrage terminées")
            _set_job(
                job_id,
                status="done",
                progress=1.0,
                message="Mises à jour de démarrage terminées",
                finished_at=_now(),
                details={
                    "started_at": started_at,
                    "current": current_result,
                    "auto": auto_result,
                },
            )
        except Exception as exc:
            _set_job(
                job_id,
                status="error",
                error=str(exc),
                message="Erreur pendant les mises à jour de démarrage",
                finished_at=_now(),
                traceback=traceback.format_exc(),
            )

    threading.Thread(target=run, name="football-prono-startup-updates", daemon=True).start()
    return job_id
