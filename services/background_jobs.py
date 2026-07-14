import datetime
import threading
import traceback
import uuid

from services import import_service


_lock = threading.RLock()
_jobs: dict[str, dict] = {}
_startup_started = False


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
            "message": "Préparation...",
            "details": details or {},
            "started_at": _now(),
            "finished_at": None,
            "error": None,
        }
    return job_id


def list_jobs() -> list[dict]:
    with _lock:
        return sorted(
            [job.copy() for job in _jobs.values()],
            key=lambda job: job.get("started_at") or "",
            reverse=True,
        )


def active_jobs() -> list[dict]:
    return [job for job in list_jobs() if job.get("status") == "running"]


def _progress(job_id: str, current: int, total: int, label: str):
    ratio = min(1.0, current / max(1, total))
    _set_job(job_id, progress=ratio, message=label)


def start_manual_import(
    league_ids: list[int],
    seasons: list[int],
    pause: float,
    max_retries: int,
    selected_presets=None,
) -> str:
    job_id = _create_job(
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


def start_startup_updates_once(connection_log_id: int | None = None) -> str | None:
    global _startup_started
    with _lock:
        if _startup_started:
            return None
        _startup_started = True

    job_id = _create_job("startup_updates", "Mises à jour de démarrage")

    def run():
        try:
            started_at = _now()
            _progress(job_id, 0, 4, "Initialisation de la base...")
            import_service.init_db()

            _progress(job_id, 1, 4, "Mise à jour des championnats en cours...")
            current_started_at = _now()
            current_result = import_service.refresh_current_competitions_on_connection()
            import_service.record_update_result("championnats_en_cours", current_started_at, current_result)
            if current_result.get("ran") and connection_log_id:
                import_service.mark_connection_current_refreshed(connection_log_id)

            _progress(job_id, 2, 4, "Synchronisation historique si nécessaire...")
            auto_started_at = _now()
            auto_result = import_service.auto_refresh_if_due()
            import_service.record_update_result("historique_auto", auto_started_at, auto_result)

            _progress(job_id, 4, 4, "Mises à jour de démarrage terminées")
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
