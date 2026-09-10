"""Archivage durable de chaque réponse réseau reçue d’API-Football."""

from __future__ import annotations

import datetime
import hashlib
import json
import threading
import time

from sqlalchemy import text

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


def ensure_table() -> None:
    global _table_ready
    if _table_ready:
        return
    with _table_lock:
        if _table_ready:
            return
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS api_response_archive (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        endpoint TEXT NOT NULL,
                        params_json TEXT NOT NULL,
                        request_sha256 TEXT NOT NULL,
                        response_sha256 TEXT NOT NULL,
                        response_json TEXT NOT NULL,
                        http_status INTEGER,
                        daily_limit INTEGER,
                        daily_remaining INTEGER,
                        minute_limit INTEGER,
                        minute_remaining INTEGER,
                        retrieved_at TEXT NOT NULL
                    )
                    """
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_api_response_archive_endpoint_time "
                    "ON api_response_archive(endpoint, retrieved_at)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_api_response_archive_request "
                    "ON api_response_archive(request_sha256)"
                )
            )
        _table_ready = True


def _integer(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def archive_response(
    endpoint: str,
    params: dict | None,
    payload,
    response_headers=None,
    http_status: int | None = None,
) -> int:
    """Persiste le contenu brut avant toute extraction métier."""
    ensure_table()
    params_json = _json(params or {})
    response_json = _json(payload)
    request_json = _json({"endpoint": str(endpoint), "params": params or {}})
    headers = response_headers if hasattr(response_headers, "get") else {}
    values = {
        "endpoint": str(endpoint),
        "params_json": params_json,
        "request_sha256": hashlib.sha256(request_json.encode("utf-8")).hexdigest(),
        "response_sha256": hashlib.sha256(response_json.encode("utf-8")).hexdigest(),
        "response_json": response_json,
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
                result = conn.execute(
                    text(
                        """
                        INSERT INTO api_response_archive (
                            endpoint, params_json, request_sha256, response_sha256,
                            response_json, http_status, daily_limit, daily_remaining,
                            minute_limit, minute_remaining, retrieved_at
                        ) VALUES (
                            :endpoint, :params_json, :request_sha256, :response_sha256,
                            :response_json, :http_status, :daily_limit, :daily_remaining,
                            :minute_limit, :minute_remaining, :retrieved_at
                        )
                        """
                    ),
                    values,
                )
            return int(result.lastrowid)
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(0.2 * (attempt + 1))
    raise RuntimeError(
        "Réponse API reçue mais impossible à archiver dans la base persistante."
    ) from last_error
