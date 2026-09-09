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
from services import full_sync_service, import_service, sync_registry


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

    started_at = _now().isoformat()
    attempt = int((previous or {}).get("attempts") or 0) + 1
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
    return {"status": status, **metadata}


def main() -> int:
    try:
        result = run()
        print(
            "Synchronisation planifiée : "
            f"{result.get('status')} · "
            f"{int(result.get('api_calls') or 0)} appel(s) API"
        )
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
