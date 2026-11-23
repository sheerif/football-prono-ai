import datetime
import json

from sqlalchemy import text

from database.database import engine


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def ensure_table() -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS resource_sync_state (
                    resource_key TEXT PRIMARY KEY,
                    resource_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    item_count INTEGER NOT NULL DEFAULT 0,
                    page_count INTEGER NOT NULL DEFAULT 0,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    message TEXT,
                    metadata_json TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
        )


def ensure_lease_table() -> None:
    """Create a durable cross-process lease table for download claims."""
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS download_task_leases (
                    resource_key TEXT PRIMARY KEY,
                    owner TEXT NOT NULL,
                    claimed_at TEXT NOT NULL,
                    lease_until TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
        )


def claim_download(
    resource_key: str,
    owner: str,
    *,
    lease_seconds: int = 900,
) -> bool:
    """Atomically claim a resource, preventing duplicate workers across processes.

    An existing claim may be replaced only after expiry or renewed by its owner.
    SQLite's conditional UPSERT is the atomic arbitration point.
    """
    key = str(resource_key)
    claimant = str(owner)
    now = _now()
    lease_until = now + datetime.timedelta(seconds=max(1, int(lease_seconds)))
    ensure_lease_table()
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                INSERT INTO download_task_leases (
                    resource_key, owner, claimed_at, lease_until, updated_at
                ) VALUES (:key, :owner, :now, :lease_until, :now)
                ON CONFLICT(resource_key) DO UPDATE SET
                    owner = excluded.owner,
                    claimed_at = excluded.claimed_at,
                    lease_until = excluded.lease_until,
                    updated_at = excluded.updated_at
                WHERE download_task_leases.lease_until <= :now
                   OR download_task_leases.owner = :owner
                """
            ),
            {
                "key": key,
                "owner": claimant,
                "now": now.isoformat(),
                "lease_until": lease_until.isoformat(),
            },
        )
    return result.rowcount == 1


def renew_download_claim(
    resource_key: str,
    owner: str,
    *,
    lease_seconds: int = 900,
) -> bool:
    """Extend a lease only when the caller still owns it."""
    now = _now()
    lease_until = now + datetime.timedelta(seconds=max(1, int(lease_seconds)))
    ensure_lease_table()
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                UPDATE download_task_leases
                SET lease_until = :lease_until, updated_at = :now
                WHERE resource_key = :key AND owner = :owner
                  AND lease_until > :now
                """
            ),
            {
                "key": str(resource_key),
                "owner": str(owner),
                "now": now.isoformat(),
                "lease_until": lease_until.isoformat(),
            },
        )
    return result.rowcount == 1


def release_download_claim(resource_key: str, owner: str) -> bool:
    """Release a claim only for its owner; a stale worker cannot unlock it."""
    ensure_lease_table()
    with engine.begin() as conn:
        result = conn.execute(
            text(
                "DELETE FROM download_task_leases "
                "WHERE resource_key = :key AND owner = :owner"
            ),
            {"key": str(resource_key), "owner": str(owner)},
        )
    return result.rowcount == 1


def get(resource_key: str) -> dict | None:
    ensure_table()
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT * FROM resource_sync_state WHERE resource_key = :key"),
            {"key": str(resource_key)},
        ).mappings().first()
    if not row:
        return None
    result = dict(row)
    try:
        result["metadata"] = json.loads(result.get("metadata_json") or "{}")
    except (TypeError, ValueError):
        result["metadata"] = {}
    return result


def should_download(
    resource_key: str,
    unavailable_retry_hours: int = 12,
    running_retry_minutes: int = 30,
) -> bool:
    row = get(resource_key)
    if not row:
        return True
    if row["status"] == "complete":
        return False
    if row["status"] == "running":
        # A running marker acts as a short lease: avoid duplicate work while
        # the current worker is active, but allow recovery after a crashed run.
        try:
            updated_at = datetime.datetime.fromisoformat(row["updated_at"])
            if updated_at.tzinfo is not None:
                updated_at = updated_at.astimezone(datetime.UTC).replace(tzinfo=None)
            return (
                (_now() - updated_at).total_seconds()
                >= max(1, int(running_retry_minutes)) * 60
            )
        except (TypeError, ValueError):
            return True
    if row["status"] != "unavailable":
        return True
    try:
        updated_at = datetime.datetime.fromisoformat(row["updated_at"])
        return (_now() - updated_at).total_seconds() >= unavailable_retry_hours * 3600
    except Exception:
        return True


def mark(
    resource_key: str,
    resource_type: str,
    status: str,
    *,
    item_count: int = 0,
    page_count: int = 0,
    message: str | None = None,
    metadata: dict | None = None,
) -> None:
    ensure_table()
    previous = get(resource_key)
    attempts = int((previous or {}).get("attempts") or 0)
    if status in {"running", "error"}:
        attempts += 1
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO resource_sync_state (
                    resource_key, resource_type, status, item_count, page_count,
                    attempts, message, metadata_json, updated_at
                )
                VALUES (
                    :resource_key, :resource_type, :status, :item_count, :page_count,
                    :attempts, :message, :metadata_json, :updated_at
                )
                ON CONFLICT(resource_key) DO UPDATE SET
                    resource_type = excluded.resource_type,
                    status = excluded.status,
                    item_count = excluded.item_count,
                    page_count = excluded.page_count,
                    attempts = excluded.attempts,
                    message = excluded.message,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """
            ),
            {
                "resource_key": str(resource_key),
                "resource_type": str(resource_type),
                "status": str(status),
                "item_count": int(item_count or 0),
                "page_count": int(page_count or 0),
                "attempts": attempts,
                "message": message,
                "metadata_json": json.dumps(metadata or {}, ensure_ascii=False, default=str),
                "updated_at": _now().isoformat(),
            },
        )


def mark_in_session(
    session,
    resource_key: str,
    resource_type: str,
    status: str,
    *,
    item_count: int = 0,
    page_count: int = 0,
    message: str | None = None,
    metadata: dict | None = None,
) -> None:
    """Write a checkpoint in the caller's SQL transaction.

    Fixture rows and their next-page checkpoint commit or roll back together.
    The caller must ensure the registry table exists before opening the session.
    """
    previous = session.execute(
        text("SELECT attempts FROM resource_sync_state WHERE resource_key = :key"),
        {"key": str(resource_key)},
    ).mappings().first()
    attempts = int((previous or {}).get("attempts") or 0)
    if status in {"running", "error"}:
        attempts += 1
    session.execute(
        text(
            """
            INSERT INTO resource_sync_state (
                resource_key, resource_type, status, item_count, page_count,
                attempts, message, metadata_json, updated_at
            )
            VALUES (
                :resource_key, :resource_type, :status, :item_count, :page_count,
                :attempts, :message, :metadata_json, :updated_at
            )
            ON CONFLICT(resource_key) DO UPDATE SET
                resource_type = excluded.resource_type,
                status = excluded.status,
                item_count = excluded.item_count,
                page_count = excluded.page_count,
                attempts = excluded.attempts,
                message = excluded.message,
                metadata_json = excluded.metadata_json,
                updated_at = excluded.updated_at
            """
        ),
        {
            "resource_key": str(resource_key),
            "resource_type": str(resource_type),
            "status": str(status),
            "item_count": int(item_count or 0),
            "page_count": int(page_count or 0),
            "attempts": attempts,
            "message": message,
            "metadata_json": json.dumps(metadata or {}, ensure_ascii=False, default=str),
            "updated_at": _now().isoformat(),
        },
    )


def counts() -> dict[str, int]:
    ensure_table()
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT status, COUNT(*) AS count FROM resource_sync_state "
                "WHERE resource_type <> 'full_sync_control' GROUP BY status"
            )
        ).fetchall()
    return {str(status): int(count) for status, count in rows}
