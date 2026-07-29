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


def get(resource_key: str) -> dict | None:
    ensure_table()
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT * FROM resource_sync_state WHERE resource_key = :key"),
            {"key": str(resource_key)},
        ).mappings().first()
    return dict(row) if row else None


def should_download(resource_key: str, unavailable_retry_hours: int = 12) -> bool:
    row = get(resource_key)
    if not row:
        return True
    if row["status"] == "complete":
        return False
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


def counts() -> dict[str, int]:
    ensure_table()
    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT status, COUNT(*) AS count FROM resource_sync_state GROUP BY status")
        ).fetchall()
    return {str(status): int(count) for status, count in rows}
