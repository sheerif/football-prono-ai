"""Shared API-Football quota reservations in coarse, conservative blocks.

Cloud and GitHub Actions use different SQLite files. This allocator uses the
remote coordination branch to reserve daily quota blocks globally; each worker
consumes its own block locally. Blocks are never refunded after a crash, which
can waste quota but prevents two restored databases from spending the same
reservation. Exhausted block IDs are reported on the next allocation.
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import json
import os
import uuid
from urllib.parse import quote

from services import api_quota_policy, git_backup, remote_sync_lease

_REMOTE_PREFIX = "api-quota-ledger"
_SCHEMA_VERSION = 1
_LOCK_SECONDS = 60
_URGENT_SCORE = api_quota_policy.PRIORITIES["prediction_blocking"]


class RemoteQuotaUnavailable(RuntimeError):
    """A shared quota reservation could not be verified."""


def enabled() -> bool:
    """Auto-enable when the runtime has the GitHub coordination token."""
    configured = os.getenv("API_QUOTA_REMOTE_RESERVATIONS")
    if configured is not None:
        return configured.strip().lower() in {"1", "true", "yes", "on"}
    try:
        return bool(git_backup._backup_token())
    except Exception:
        return False


def allocation_owner() -> str:
    explicit = os.getenv("API_QUOTA_REMOTE_OWNER")
    if explicit:
        return explicit.strip()[:160]
    if os.getenv("GITHUB_ACTIONS", "").lower() == "true":
        return ":".join(
            part for part in (
                "actions",
                os.getenv("GITHUB_RUN_ID", "unknown"),
                os.getenv("GITHUB_JOB", "sync"),
                os.getenv("GITHUB_RUN_ATTEMPT", "1"),
            ) if part
        )[:160]
    return ("runtime:" + (os.getenv("HOSTNAME") or "streamlit"))[:160]


def _utc_now(value: datetime.datetime | None = None) -> datetime.datetime:
    value = value or datetime.datetime.now(datetime.UTC)
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.UTC)
    return value.astimezone(datetime.UTC)


def _int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _block_size() -> int:
    return max(1, _int(os.getenv("API_QUOTA_REMOTE_BLOCK_SIZE"), 100) or 100)


def _unknown_budget() -> int:
    return max(1, _int(os.getenv("API_QUOTA_UNKNOWN_RESERVE"), 10) or 10)


def _path(day_key: str) -> str:
    digest = hashlib.sha256(day_key.encode("ascii")).hexdigest()
    return f"{_REMOTE_PREFIX}/{digest}.json"


def _read_record(repository: str, branch: str, path: str, token: str):
    endpoint = (
        f"/repos/{repository}/contents/{quote(path, safe='/')}"
        f"?ref={quote(branch, safe='')}"
    )
    try:
        response = git_backup._request("GET", endpoint, token)
    except RuntimeError as exc:
        if remote_sync_lease._is_http_error(exc, 404):
            return None, None
        raise
    try:
        raw = base64.b64decode(response.get("content") or "").decode("utf-8")
        record = json.loads(raw)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RemoteQuotaUnavailable("Ledger quota distant illisible.") from exc
    if not isinstance(record, dict) or record.get("schema_version") != _SCHEMA_VERSION:
        raise RemoteQuotaUnavailable("Version du ledger quota distant inconnue.")
    if record.get("quota_day") != path.split("/")[-1].split(".")[0]:
        # The path is hashed; callers validate the day field separately.
        pass
    return record, response.get("sha")


def _write_record(
    repository: str, branch: str, path: str, token: str,
    record: dict, sha: str | None,
) -> bool:
    payload = {
        "message": "Reserve shared API-Football quota block",
        "content": base64.b64encode(
            (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()
        ).decode("ascii"),
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha
    try:
        git_backup._request(
            "PUT", f"/repos/{repository}/contents/{quote(path, safe='/')}",
            token, payload,
        )
        return True
    except RuntimeError as exc:
        if remote_sync_lease._is_http_error(exc, 409) or remote_sync_lease._is_http_error(exc, 422):
            return False
        raise


def _initial_record(day_key: str, snapshot: dict, now: datetime.datetime) -> dict:
    remaining = _int(snapshot.get("daily_remaining"))
    limit = _int(snapshot.get("daily_limit"))
    known = bool(snapshot.get("known")) and remaining is not None and limit is not None
    return {
        "schema_version": _SCHEMA_VERSION,
        "quota_day": day_key,
        "known": known,
        "daily_limit": limit if known else None,
        "lowest_observed_remaining": max(0, remaining) if known else None,
        "reserve": api_quota_policy.reserve_for(snapshot) if known else 0,
        "available": max(0, remaining) if known else _unknown_budget(),
        "allocations": {},
        "updated_at": now.isoformat(),
    }


def _merge_snapshot(record: dict, snapshot: dict) -> bool:
    """Lower the global ceiling from fresh status, never raise it from stale data."""
    remaining = _int(snapshot.get("daily_remaining"))
    limit = _int(snapshot.get("daily_limit"))
    known = bool(snapshot.get("known")) and remaining is not None and limit is not None
    if not known:
        return False
    outstanding = sum(
        max(0, _int(item.get("remaining"), 0) or 0)
        for item in record.get("allocations", {}).values()
        if isinstance(item, dict)
    )
    previous_low = _int(record.get("lowest_observed_remaining"))
    low = max(0, remaining) if previous_low is None else min(previous_low, max(0, remaining))
    candidate = max(0, low - outstanding)
    current_available = max(0, _int(record.get("available"), 0) or 0)
    record["available"] = min(current_available, candidate)
    record["lowest_observed_remaining"] = low
    previous_limit = _int(record.get("daily_limit"))
    record["daily_limit"] = limit if previous_limit is None else min(previous_limit, limit)
    record["known"] = True
    record["reserve"] = max(
        max(0, _int(record.get("reserve"), 0) or 0),
        api_quota_policy.reserve_for(snapshot),
    )
    return True


def reserve_block(
    snapshot: dict,
    priority: str,
    *,
    spent_allocation_ids: tuple[str, ...] | list[str] = (),
    now: datetime.datetime | None = None,
) -> dict:
    """CAS-reserve a block for one runtime, or reject without granting tokens."""
    if not enabled():
        return {"allowed": False, "reason": "remote_reservations_disabled", "remote": False}
    snapshot = dict(snapshot or {})
    current = _utc_now(now)
    day_key = current.date().isoformat()
    lock_key = f"api-quota-ledger:{day_key}"
    lock_owner = f"quota-{uuid.uuid4().hex}"
    if not remote_sync_lease.claim(lock_key, lock_owner, lease_seconds=_LOCK_SECONDS):
        return {"allowed": False, "reason": "remote_quota_lock_contention", "remote": True}
    reported = False
    try:
        repository, token = remote_sync_lease._repo_token()
        branch = remote_sync_lease._branch()
        path = _path(day_key)
        record, sha = _read_record(repository, branch, path, token)
        if record is None:
            record = _initial_record(day_key, snapshot, current)
        if record.get("quota_day") != day_key:
            raise RemoteQuotaUnavailable("Date du ledger quota distant incohérente.")

        allocations = record.setdefault("allocations", {})
        spent_changed = False
        for allocation_id in spent_allocation_ids:
            allocation = allocations.get(str(allocation_id))
            if isinstance(allocation, dict) and int(allocation.get("remaining") or 0) > 0:
                allocation["remaining"] = 0
                allocation["spent_reported_at"] = current.isoformat()
                spent_changed = True

        known = bool(record.get("known"))
        snapshot_known = (
            bool(snapshot.get("known"))
            and _int(snapshot.get("daily_remaining")) is not None
            and _int(snapshot.get("daily_limit")) is not None
        )
        if snapshot_known:
            _merge_snapshot(record, snapshot)
        outstanding = sum(
            max(0, _int(item.get("remaining"), 0) or 0)
            for item in allocations.values()
            if isinstance(item, dict)
        )
        available = max(0, _int(record.get("available"), 0) or 0)
        reserve = max(0, _int(record.get("reserve"), 0) or 0)
        urgent = api_quota_policy.PRIORITIES.get(priority, 0) >= _URGENT_SCORE
        max_allocatable = available if urgent else max(0, available - reserve)
        amount = min(_block_size(), max_allocatable)

        if amount <= 0:
            # Report exhausted blocks even when no replacement can be granted.
            if spent_changed or snapshot_known:
                record["updated_at"] = current.isoformat()
                reported = _write_record(repository, branch, path, token, record, sha)
            return {
                "allowed": False,
                "reason": "remote_shared_quota_exhausted" if known or snapshot_known else "remote_unknown_quota_exhausted",
                "remaining": available,
                "reserve": reserve,
                "known": bool(record.get("known")),
                "spent_reported": reported,
                "remote": True,
            }

        allocation_id = uuid.uuid4().hex
        allocations[allocation_id] = {
            "owner": allocation_owner(),
            "priority_class": "urgent" if urgent else "normal",
            "amount": amount,
            "remaining": amount,
            "created_at": current.isoformat(),
        }
        record["available"] = available - amount
        record["updated_at"] = current.isoformat()
        written = _write_record(repository, branch, path, token, record, sha)
        if not written:
            return {
                "allowed": False,
                "reason": "remote_shared_quota_cas_conflict",
                "remaining": available,
                "reserve": reserve,
                "known": bool(record.get("known")),
                "spent_reported": False,
                "remote": True,
            }
        reported = True
        return {
            "allowed": True,
            "reason": "remote_block_reserved",
            "allocation_id": allocation_id,
            "amount": amount,
            "remaining": amount,
            "reserve": reserve,
            "known": bool(record.get("known")),
            "spent_reported": reported,
            "remote": True,
        }
    finally:
        remote_sync_lease.release(lock_key, lock_owner)
