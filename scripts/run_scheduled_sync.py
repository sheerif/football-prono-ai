"""Exécute la synchronisation quotidienne contre la base persistante.

Ce point d'entrée est utilisé par GitHub Actions lorsque Streamlit Cloud est
endormi. Il partage le même verrou durable que l'interface et ne lance donc pas
un second traitement si Streamlit a déjà repris la tâche à minuit UTC.
"""

from __future__ import annotations

import datetime
import logging
import os
import sys
import time
import uuid


ROOT = os.path.dirname(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from database.database import engine
from services import background_jobs, full_sync_service, git_backup, import_service, prediction_archive_service, remote_sync_lease, sync_registry


CONTROL_KEY = "full-sync:exhaustive"
LEASE_SECONDS = 900
LEASE_RENEW_INTERVAL_SECONDS = 60


class FullSyncLeaseLost(RuntimeError):
    pass


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def _log_progress(current: int, total: int, label: str) -> None:
    total = max(1, int(total or 1))
    current = max(0, int(current or 0))
    percent = min(100, int(round(current / total * 100)))
    logging.info("Scheduled sync progress %s/%s (%s%%): %s", current, total, percent, label)


def run() -> dict:
    import_service.init_db()
    sync_registry.ensure_table()
    owner = f"scheduled-sync:{uuid.uuid4().hex}"
    if not remote_sync_lease.claim(CONTROL_KEY, owner, lease_seconds=LEASE_SECONDS):
        return {"status": "skipped", "reason": "lease distante de synchronisation détenue"}
    try:
        local_claimed = sync_registry.claim_download(
            CONTROL_KEY, owner, lease_seconds=LEASE_SECONDS
        )
    except Exception:
        remote_sync_lease.release(CONTROL_KEY, owner)
        raise
    if not local_claimed:
        remote_sync_lease.release(CONTROL_KEY, owner)
        return {"status": "skipped", "reason": "lease locale de synchronisation détenue"}

    lease_lost = False
    last_renewed = time.monotonic()
    started_at = _now().isoformat()
    attempt = 1

    def renew_lease(*, force: bool = False) -> None:
        nonlocal last_renewed, lease_lost
        now = time.monotonic()
        if not force and now - last_renewed < LEASE_RENEW_INTERVAL_SECONDS:
            return
        try:
            remote_renewed = remote_sync_lease.renew(
                CONTROL_KEY, owner, lease_seconds=LEASE_SECONDS
            )
        except Exception as exc:
            lease_lost = True
            raise FullSyncLeaseLost(
                "Impossible de vérifier la lease distante de synchronisation."
            ) from exc
        if not remote_renewed:
            lease_lost = True
            raise FullSyncLeaseLost("Lease distante globale de synchronisation perdue.")
        try:
            local_renewed = sync_registry.renew_download_claim(
                CONTROL_KEY, owner, lease_seconds=LEASE_SECONDS
            )
        except Exception as exc:
            lease_lost = True
            raise FullSyncLeaseLost(
                "Impossible de vérifier la lease locale de synchronisation."
            ) from exc
        if not local_renewed:
            lease_lost = True
            raise FullSyncLeaseLost("Lease locale globale de synchronisation perdue.")
        last_renewed = now

    def progress(current: int, total: int, label: str) -> None:
        renew_lease()
        _log_progress(current, total, label)

    try:
        previous = sync_registry.get(CONTROL_KEY)
        attempt = int((previous or {}).get("attempts") or 0) + 1
        renew_lease(force=True)
        try:
            pre_match_archive = prediction_archive_service.archive_upcoming_predictions()
        except Exception as exc:
            pre_match_archive = {
                "status": "error",
                "archived": 0,
                "api_calls": 0,
                "errors": [str(exc)],
            }
        renew_lease(force=True)
        quota_status = background_jobs.api_quota_status(force=True)
        renew_lease(force=True)
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
                "lease_owner": owner,
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
                "lease_owner": owner,
            },
        )
        result = full_sync_service.run_full_sync(progress_callback=progress)
        renew_lease(force=True)

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
                "lease_owner": owner,
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
                "lease_owner": owner,
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
    except Exception as exc:
        if not lease_lost:
            sync_registry.mark(
                CONTROL_KEY,
                "full_sync_control",
                "error",
                message="Échec de la synchronisation quotidienne planifiée",
                metadata={
                    "scheduler": "github-actions",
                    "started_at": started_at,
                    "lease_owner": owner,
                },
            )
            import_service.record_update_log(
                event_type="synchronisation_planifiee",
                status="erreur",
                started_at=started_at,
                error=str(exc),
            )
        raise
    finally:
        try:
            sync_registry.release_download_claim(CONTROL_KEY, owner)
        except Exception:
            logging.warning("Unable to release scheduled local full-sync lease", exc_info=True)
        try:
            remote_sync_lease.release(CONTROL_KEY, owner)
        except Exception:
            logging.warning("Unable to release scheduled remote full-sync lease", exc_info=True)


def main() -> int:
    try:
        result = run()
        status = result.get("status")
        checkpoint_required = status in {"waiting_quota", "partial"}
        backup = (
            {"status": "skipped_active_sync"}
            if status == "skipped"
            else git_backup.backup_to_github(force=checkpoint_required)
        )
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
        if result.get("status") == "skipped":
            print(
                "Synchronisation ignorée : une autre instance possède la lease globale. "
                "Ce run ne constitue pas une synchronisation validée.",
                file=sys.stderr,
            )
            return 1
        if result.get("status") != "complete":
            print(
                "Synchronisation non exhaustive "
                f"(status={result.get('status')}) : le run échoue volontairement.",
                file=sys.stderr,
            )
            return 1
        if backup.get("status") not in {
            "uploaded",
            "unchanged",
            "skipped_daily_limit",
        }:
            return 1
        return 0
    except Exception as exc:
        print(f"Échec de la synchronisation planifiée : {exc}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
