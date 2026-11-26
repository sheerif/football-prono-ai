"""Exécute la synchronisation quotidienne contre la base persistante.

Ce point d'entrée est utilisé par GitHub Actions lorsque Streamlit Cloud est
endormi. Il partage le même verrou durable que l'interface et ne lance donc pas
un second traitement si Streamlit a déjà repris la tâche à minuit UTC.
"""

from __future__ import annotations

import datetime
import logging
import json
import os
import sys
import threading
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
TRANSIENT_QUOTA_KINDS = frozenset({"minute", "provider_rate_limit", "remote_lock_contention"})
MAX_TRANSIENT_QUOTA_RETRIES = 5
DEFAULT_TRANSIENT_RETRY_SECONDS = 90
MAX_TRANSIENT_RETRY_SECONDS = 300


class FullSyncLeaseLost(RuntimeError):
    pass


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def _log_progress(current: int, total: int, label: str) -> None:
    total = max(1, int(total or 1))
    current = max(0, int(current or 0))
    percent = min(100, int(round(current / total * 100)))
    logging.info("Scheduled sync progress %s/%s (%s%%): %s", current, total, percent, label)


def _optional_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _transient_retry_delay_seconds(result: dict) -> float:
    """Return a bounded wait until a persisted transient-quota retry is due."""
    try:
        retry_at = datetime.datetime.fromisoformat(str(result.get("next_retry_at") or ""))
        if retry_at.tzinfo is not None:
            retry_at = retry_at.astimezone(datetime.UTC).replace(tzinfo=None)
        delay = (retry_at - _now()).total_seconds()
        return min(
            float(MAX_TRANSIENT_RETRY_SECONDS),
            max(1.0, delay),
        )
    except (TypeError, ValueError, OverflowError):
        return float(DEFAULT_TRANSIENT_RETRY_SECONDS)


def _quota_failure_kind(error: str, status_by_family: dict | None = None) -> str:
    """Classifie la cause observée sans confondre quota journalier et blocage transitoire."""
    normalized = str(error or "").casefold()
    snapshots = (status_by_family or {}).values()
    if any(
        _optional_int((snapshot or {}).get("daily_remaining")) == 0
        for snapshot in snapshots
    ):
        return "daily"
    snapshots = (status_by_family or {}).values()
    if any(
        _optional_int((snapshot or {}).get("minute_remaining")) == 0
        for snapshot in snapshots
    ):
        return "minute"
    if "remote_quota_lock_contention" in normalized:
        return "remote_lock_contention"
    if "remote_shared_quota" in normalized:
        return "remote_shared_budget"
    if "atomic_" in normalized:
        return "local_atomic_budget"
    if "emergency_reserve" in normalized or "reserve" in normalized or "réserve" in normalized:
        return "policy_reserve"
    if "daily" in normalized or "journalier" in normalized:
        return "daily"
    if "minute" in normalized:
        return "minute"
    if "429" in normalized or "rate limit" in normalized:
        return "provider_rate_limit"
    return "unclassified"


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

    lease_lost = threading.Event()
    heartbeat_stop = threading.Event()
    renew_lock = threading.Lock()
    heartbeat_thread = None
    heartbeat_started = False
    last_renewed = time.monotonic()
    started_at = _now().isoformat()
    attempt = 1

    def renew_lease(*, force: bool = False) -> None:
        nonlocal last_renewed
        with renew_lock:
            if lease_lost.is_set():
                raise FullSyncLeaseLost(
                    "Lease globale de synchronisation déjà perdue."
                )
            now = time.monotonic()
            if not force and now - last_renewed < LEASE_RENEW_INTERVAL_SECONDS:
                return
            try:
                remote_renewed = remote_sync_lease.renew(
                    CONTROL_KEY, owner, lease_seconds=LEASE_SECONDS
                )
            except Exception as exc:
                lease_lost.set()
                raise FullSyncLeaseLost(
                    "Impossible de vérifier la lease distante de synchronisation."
                ) from exc
            if not remote_renewed:
                lease_lost.set()
                raise FullSyncLeaseLost(
                    "Lease distante globale de synchronisation perdue."
                )
            try:
                local_renewed = sync_registry.renew_download_claim(
                    CONTROL_KEY, owner, lease_seconds=LEASE_SECONDS
                )
            except Exception as exc:
                lease_lost.set()
                raise FullSyncLeaseLost(
                    "Impossible de vérifier la lease locale de synchronisation."
                ) from exc
            if not local_renewed:
                lease_lost.set()
                raise FullSyncLeaseLost(
                    "Lease locale globale de synchronisation perdue."
                )
            last_renewed = now

    def heartbeat() -> None:
        while not heartbeat_stop.wait(LEASE_RENEW_INTERVAL_SECONDS):
            try:
                renew_lease(force=True)
            except Exception:
                logging.exception("Scheduled sync lease heartbeat failed")
                return

    def progress(current: int, total: int, label: str) -> None:
        renew_lease()
        _log_progress(current, total, label)

    try:
        heartbeat_thread = threading.Thread(
            target=heartbeat,
            name="football-scheduled-sync-lease-heartbeat",
            daemon=True,
        )
        heartbeat_thread.start()
        heartbeat_started = True
        previous = sync_registry.get(CONTROL_KEY)
        attempt = int((previous or {}).get("attempts") or 0) + 1
        renew_lease(force=True)
        try:
            pre_match_archive = prediction_archive_service.archive_upcoming_predictions(
                progress_callback=progress,
            )
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
            preflight_error = str(quota_status.get("error") or message)
            preflight_snapshot = {
                "daily_remaining": quota_status.get("remaining"),
                "minute_remaining": quota_status.get("minute_remaining"),
            }
            metadata = {
                "persistent": True,
                "scheduler": "github-actions",
                "attempt": attempt,
                "started_at": started_at,
                "next_retry_at": retry_at.isoformat(),
                "quota_preflight": quota_status,
                "quota_error": preflight_error,
                "quota_kind": _quota_failure_kind(preflight_error, {"preflight": preflight_snapshot}),
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

        sync_errors = [str(item) for item in (result.get("errors") or [])]
        quota_status_by_family = dict(result.get("quota_status_by_family") or {})
        sync_diagnostics = {
            "api_calls_by_family": dict(result.get("api_calls_by_family") or {}),
            "quota_status_by_family": quota_status_by_family,
            "quota_metrics": dict(result.get("quota_metrics") or {}),
            "errors": sync_errors[-20:],
            "errors_total": len(sync_errors),
        }

        if result.get("quota_reached"):
            current = _now().replace(tzinfo=datetime.UTC)
            status = "waiting_quota"
            quota_error = sync_errors[-1] if sync_errors else str(result.get("message") or "")
            quota_kind = _quota_failure_kind(quota_error, quota_status_by_family)
            if quota_kind in {"minute", "provider_rate_limit"}:
                retry_at = current + datetime.timedelta(seconds=90)
                message = (
                    "Limite API par minute atteinte : données conservées. "
                    "Reprise possible après le délai minute, au prochain passage planifié."
                )
            elif quota_kind == "remote_lock_contention":
                retry_at = current + datetime.timedelta(seconds=60)
                message = (
                    "Verrou distant du quota temporairement occupé : données conservées. "
                    "Reprise au prochain passage planifié."
                )
            else:
                retry_at = (current + datetime.timedelta(days=1)).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                if quota_kind == "daily":
                    message = (
                        "Quota API journalier épuisé : données conservées. "
                        "Reprise automatique après minuit UTC."
                    )
                elif quota_kind == "policy_reserve":
                    message = (
                        "Réserve quotidienne protégée : données conservées. "
                        "Reprise après renouvellement du quota journalier."
                    )
                elif quota_kind in {"local_atomic_budget", "remote_shared_budget"}:
                    message = (
                        "Budget de quota atomique ou partagé épuisé : données conservées. "
                        "Diagnostic conservé et reprise après renouvellement journalier."
                    )
                else:
                    message = (
                        "Synchronisation arrêtée par une règle de quota non classée : "
                        "données conservées et reprise au prochain renouvellement journalier."
                    )
            metadata = {
                "persistent": True,
                "scheduler": "github-actions",
                "attempt": attempt,
                "started_at": started_at,
                "next_retry_at": retry_at.replace(tzinfo=None).isoformat(),
                "checkpoint": result.get("checkpoint"),
                "api_calls": int(result.get("api_calls") or 0),
                "quota_error": quota_error,
                "quota_kind": quota_kind,
                **sync_diagnostics,
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
                **sync_diagnostics,
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
        if not lease_lost.is_set():
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
        heartbeat_stop.set()
        heartbeat_stopped = True
        if heartbeat_started and heartbeat_thread is not None:
            heartbeat_thread.join(timeout=LEASE_RENEW_INTERVAL_SECONDS + 5)
            heartbeat_stopped = not heartbeat_thread.is_alive()
            if not heartbeat_stopped:
                logging.warning(
                    "Scheduled sync lease heartbeat did not stop; leaving leases to expire"
                )
        if heartbeat_stopped:
            try:
                sync_registry.release_download_claim(CONTROL_KEY, owner)
            except Exception:
                logging.warning(
                    "Unable to release scheduled local full-sync lease", exc_info=True
                )
            try:
                remote_sync_lease.release(CONTROL_KEY, owner)
            except Exception:
                logging.warning(
                    "Unable to release scheduled remote full-sync lease", exc_info=True
                )


def main() -> int:
    retries = 0
    try:
        while True:
            try:
                result = run()
            except Exception:
                # run() releases its local and remote sync leases in finally.
                # Reacquire the shared sync gate through backup_to_github before
                # snapshotting, so an exception cannot discard a committed
                # checkpoint and cannot race a replacement sync worker.
                try:
                    backup = git_backup.backup_to_github(force=True)
                    if (
                        backup.get("status") not in {"uploaded", "unchanged"}
                        or backup.get("remote_verified") is not True
                    ):
                        print(
                            "Checkpoint post-erreur non vérifié à distance "
                            f"(backup={backup.get('status', 'unknown')}).",
                            file=sys.stderr,
                        )
                    else:
                        print(
                            "Checkpoint post-erreur vérifié à distance "
                            f"(backup={backup.get('status')}).",
                            file=sys.stderr,
                        )
                except Exception as backup_error:
                    print(
                        "Sauvegarde du checkpoint post-erreur impossible : "
                        f"{backup_error}",
                        file=sys.stderr,
                    )
                raise
            status = result.get("status")
            # Persist each partial checkpoint before waiting for a transient
            # minute/rate-limit window to reset. A retry is never attempted
            # unless the remote snapshot has been verified.
            backup = (
                {"status": "skipped_active_sync"}
                if status == "skipped"
                else git_backup.backup_to_github(force=True)
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
            if status == "waiting_quota":
                print(
                    "Diagnostic quota : "
                    f"type={result.get('quota_kind', 'non_classé')} · "
                    f"checkpoint={result.get('checkpoint') or 'non précisé'}"
                )
                if result.get("quota_error"):
                    print(f"Cause quota : {result['quota_error']}")
                if result.get("quota_status_by_family"):
                    print(
                        "Compteurs quota par famille : "
                        + json.dumps(result["quota_status_by_family"], ensure_ascii=False, sort_keys=True)
                    )
                if result.get("quota_metrics"):
                    print(
                        "Mesures quota : "
                        + json.dumps(result["quota_metrics"], ensure_ascii=False, sort_keys=True)
                    )
                if result.get("quota_preflight"):
                    print(
                        "Quota préflight : "
                        + json.dumps(result["quota_preflight"], ensure_ascii=False, sort_keys=True)
                    )
            if status == "skipped":
                print(
                    "Synchronisation ignorée : une autre instance possède la lease globale. "
                    "Ce run ne constitue pas une synchronisation validée.",
                    file=sys.stderr,
                )
                return 1
            if (
                backup.get("status") not in {"uploaded", "unchanged"}
                or backup.get("remote_verified") is not True
            ):
                print(
                    "Sauvegarde post-sync non vérifiée à distance.",
                    file=sys.stderr,
                )
                return 1
            quota_kind = str(result.get("quota_kind") or "")
            if (
                status == "waiting_quota"
                and quota_kind in TRANSIENT_QUOTA_KINDS
                and retries < MAX_TRANSIENT_QUOTA_RETRIES
            ):
                delay = _transient_retry_delay_seconds(result)
                retries += 1
                print(
                    "Quota transitoire : checkpoint distant vérifié ; "
                    f"reprise {retries}/{MAX_TRANSIENT_QUOTA_RETRIES} "
                    f"dans {delay:.0f} seconde(s)."
                )
                time.sleep(delay)
                continue
            if status != "complete":
                print(
                    "Synchronisation non exhaustive "
                    f"(status={status}) : le run échoue volontairement.",
                    file=sys.stderr,
                )
                return 1
            return 0
    except Exception as exc:
        print(f"Échec de la synchronisation planifiée : {exc}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()

if __name__ == "__main__":
    raise SystemExit(main())
