"""Conserve le dernier état technique de chaque requête API, sans archiver les payloads."""

from __future__ import annotations

import datetime
import hashlib
import json
import threading
import time

from sqlalchemy import inspect, text

from database.database import engine


_table_ready = False
_table_lock = threading.RLock()


def _now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None).isoformat()


def _json(value) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _integer(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def ensure_table() -> None:
    """Create the compact request-state table and migrate the old raw archive once."""
    global _table_ready
    if _table_ready:
        return
    with _table_lock:
        if _table_ready:
            return
        with engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS api_request_state (
                    request_sha256 TEXT PRIMARY KEY,
                    endpoint TEXT NOT NULL,
                    response_sha256 TEXT NOT NULL,
                    http_status INTEGER,
                    daily_limit INTEGER,
                    daily_remaining INTEGER,
                    minute_limit INTEGER,
                    minute_remaining INTEGER,
                    retrieved_at TEXT NOT NULL
                )
            """))
            inspector = inspect(conn)
            if inspector.has_table("api_response_archive"):
                columns = {item["name"] for item in inspector.get_columns("api_response_archive")}
                required = {
                    "endpoint", "request_sha256", "response_sha256", "http_status",
                    "daily_limit", "daily_remaining", "minute_limit",
                    "minute_remaining", "retrieved_at", "id",
                }
                if required.issubset(columns):
                    # Keep one compact state per request fingerprint; never copy raw JSON.
                    conn.execute(text("""
                        INSERT OR REPLACE INTO api_request_state (
                            request_sha256, endpoint, response_sha256, http_status,
                            daily_limit, daily_remaining, minute_limit,
                            minute_remaining, retrieved_at
                        )
                        SELECT old.request_sha256, old.endpoint, old.response_sha256,
                               old.http_status, old.daily_limit, old.daily_remaining,
                               old.minute_limit, old.minute_remaining, old.retrieved_at
                        FROM api_response_archive AS old
                        JOIN (
                            SELECT request_sha256, MAX(id) AS latest_id
                            FROM api_response_archive
                            GROUP BY request_sha256
                        ) AS latest ON latest.latest_id = old.id
                    """))
                conn.execute(text("DROP TABLE api_response_archive"))
        _table_ready = True


def archive_response(
    endpoint: str,
    params: dict | None,
    payload,
    response_headers=None,
    http_status: int | None = None,
) -> int:
    """Replace the previous technical state for this endpoint/parameter set."""
    ensure_table()
    request_json = _json({"endpoint": str(endpoint), "params": params or {}})
    response_json = _json(payload)
    headers = response_headers if hasattr(response_headers, "get") else {}
    values = {
        "endpoint": str(endpoint),
        "request_sha256": hashlib.sha256(request_json.encode("utf-8")).hexdigest(),
        "response_sha256": hashlib.sha256(response_json.encode("utf-8")).hexdigest(),
        "http_status": _integer(http_status),
        "daily_limit": _integer(headers.get("x-ratelimit-requests-limit")),
        "daily_remaining": _integer(headers.get("x-ratelimit-requests-remaining")),
        "minute_limit": _integer(headers.get("X-RateLimit-Limit")),
        "minute_remaining": _integer(headers.get("X-RateLimit-Remaining")),
        "retrieved_at": _now_iso(),
    }
    last_error = None
    for attempt in range(3):
        try:
            with engine.begin() as conn:
                conn.execute(text("""
                    INSERT INTO api_request_state (
                        request_sha256, endpoint, response_sha256, http_status,
                        daily_limit, daily_remaining, minute_limit,
                        minute_remaining, retrieved_at
                    ) VALUES (
                        :request_sha256, :endpoint, :response_sha256, :http_status,
                        :daily_limit, :daily_remaining, :minute_limit,
                        :minute_remaining, :retrieved_at
                    )
                    ON CONFLICT(request_sha256) DO UPDATE SET
                        endpoint = excluded.endpoint,
                        response_sha256 = excluded.response_sha256,
                        http_status = excluded.http_status,
                        daily_limit = excluded.daily_limit,
                        daily_remaining = excluded.daily_remaining,
                        minute_limit = excluded.minute_limit,
                        minute_remaining = excluded.minute_remaining,
                        retrieved_at = excluded.retrieved_at
                """), values)
            return 1
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(0.2 * (attempt + 1))
    raise RuntimeError(
        "Réponse API reçue mais impossible d'actualiser son état technique."
    ) from last_error
