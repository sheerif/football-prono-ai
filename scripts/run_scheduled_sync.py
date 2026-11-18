"""Exécute la synchronisation quotidienne contre la base persistante.

Ce point d'entrée est utilisé par GitHub Actions lorsque Streamlit Cloud est
endormi. Il partage le même verrou durable que l'interface et ne lance donc pas
un second traitement si Streamlit a déjà repris la tâche à minuit UTC.
"""

from __future__ import annotations

import datetime
import os
import sys


ROOT = os.path.dirname(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from database.database import engine
from services import background_jobs, full_sync_service, git_backup, import_service, prediction_archive_service, sync_registry


CONTROL_KEY = "full-sync:exhaustive"


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def _recent_running_job(state: dict | None, *, max_age_hours: int = 6) -> bool:
    if not state or state.get("status") != "running":
        return False
    try:
        updated_at = datetime.datetime.fromisoformat(str(state["updated_at"]))
    except (KeyError, TypeError, ValueError):
        return False
    return (_now() - updated_at).total_seconds() < max_age_hours * 3600


def run() -> dict:
    import_service.init_db()
    sync_registry.ensure_table()
    previous = sync_registry.get(CONTROL_KEY)
    if _recent_running_job(previous):
        return {"status": "skipped", "reason": "synchronisation déjà active"}

    try:
        pre_match_archive = prediction_archive_service.archive_upcoming_predictions()
    except Exception as exc:
        pre_match_archive = {
            "status": "error",
            "archived": 0,
            "api_calls": 0,
            "errors": [str(exc)],
        }

    started_at = _now().isoformat()
    attempt = int((previous or {}).get("attempts") or 0) + 1
    quota_status = background_jobs.api_quota_status(force=True)
    if background_jobs._quota_is_exhausted(quota_status):
        now = datetime.datetime.now(datetime.UTC)
        minute_exhausted = (
            quota_status.get("minute_remaining") is not None
            and int(quota_status["minute_remaining"]) <= 0
        ) or background_jobs._is_minute_quota(
            {"errors": [quota_status.get("error", "")]}
        )
        retry_at = (
            now + datetime.timedelta(seconds=90)
            if minute_exhausted
            else (now + datetime.timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
        )
        retry_at = retry_at.replace(tzinfo=None)
        message = (
            "Limite API par minute atteinte : synchronisation reportée."
            if minute_exhausted
            else "Quota API quotidien épuisé : synchronisation reportée."
        )
        metadata = {
            "persistent": True,
            "scheduler": "github-actions",
            "attempt": attempt,
            "started_at": started_at,
            "next_retry_at": retry_at.isoformat(),
            "quota_preflight": quota_status,
            "pre_match_archive": pre_match_archive,
        }
        sync_registry.mark(
            CONTROL_KEY,
            "full_sync_control",
            "waiting_quota",
            message=message,
            metadata=metadata,
        )
        import_service.record_update_log(
            event_type="synchronisation_planifiee",
            status="en_attente_quota",
            started_at=started_at,
            reason=message,
            details=metadata,
        )
        return {"status": "waiting_quota", **metadata, "pre_match_archive": pre_match_archive}

    sync_registry.mark(
        CONTROL_KEY,
        "full_sync_control",
        "running",
        message="Synchronisation quotidienne planifiée",
        metadata={
            "persistent": True,
            "scheduler": "github-actions",
            "attempt": attempt,
            "started_at": started_at,
        },
    )
    try:
        result = full_sync_service.run_full_sync()
    except Exception as exc:
        sync_registry.mark(
            CONTROL_KEY,
            "full_sync_control",
            "error",
            message="Échec de la synchronisation quotidienne planifiée",
            metadata={"scheduler": "github-actions", "started_at": started_at},
        )
        import_service.record_update_log(
            event_type="synchronisation_planifiee",
            status="erreur",
            started_at=started_at,
            error=str(exc),
        )
        raise

    if result.get("quota_reached"):
        current = _now().replace(tzinfo=datetime.UTC)
        next_midnight = (current + datetime.timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).replace(tzinfo=None)
        status = "waiting_quota"
        message = (
            "Quota API atteint : données conservées. "
            "Reprise automatique après minuit UTC."
        )
        metadata = {
            "persistent": True,
            "scheduler": "github-actions",
            "attempt": attempt,
            "started_at": started_at,
            "next_retry_at": next_midnight.isoformat(),
            "checkpoint": result.get("checkpoint"),
            "api_calls": int(result.get("api_calls") or 0),
            "pre_match_archive": pre_match_archive,
        }
    else:
        partial = bool(result.get("partial") or result.get("errors"))
        status = "partial" if partial else "complete"
        message = (
            "Synchronisation quotidienne terminée avec réserves."
            if partial
            else "Synchronisation quotidienne terminée."
        )
        metadata = {
            "persistent": True,
            "scheduler": "github-actions",
            "attempt": attempt,
            "started_at": started_at,
            "api_calls": int(result.get("api_calls") or 0),
            "xg_api_calls": int(result.get("xg_api_calls") or 0),
            "xg_budget_reserved": bool(result.get("xg_budget_reserved")),
            "pre_match_archive": pre_match_archive,
        }

    sync_registry.mark(
        CONTROL_KEY,
        "full_sync_control",
        status,
        message=message,
        metadata=metadata,
    )
    import_service.record_update_log(
        event_type="synchronisation_planifiee",
        status=status,
        started_at=started_at,
        reason=message,
        details=metadata,
    )
    return {"status": status, **metadata, "pre_match_archive": pre_match_archive}


def main() -> int:
    try:
        result = run()
        backup = git_backup.backup_to_github(force=True)
        result["database_backup"] = backup
        print(
            "Synchronisation planifiée : "
            f"{result.get('status')} · "
            f"{int(result.get('api_calls') or 0)} appel(s) API · "
            f"archivage pré-match : "
            f"{int((result.get('pre_match_archive') or {}).get('archived') or 0)} "
            "prévision(s) · "
            f"sauvegarde : {backup.get('status', 'unknown')}"
        )
        if backup.get("status") not in {"uploaded", "unchanged", "not_configured"}:
            return 1
        return 0
    except Exception as exc:
        print(f"Échec de la synchronisation planifiée : {exc}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
