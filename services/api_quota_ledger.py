"""Atomic daily API-Football budget reservations across SQLite processes.

Each uncached data request reserves one token before network I/O. SQLite's
BEGIN IMMEDIATE serializes reservations made by independent workers sharing the
same database file. The ledger is conservative: a token is not refunded after
a request attempt, and newer quota snapshots can only lower the local budget.
"""
from __future__ import annotations

import datetime
import os

from sqlalchemy import text

from database.database import engine
from services import api_quota_policy

_TABLE = "api_quota_ledger"
_URGENT_PRIORITY = api_quota_policy.PRIORITIES["prediction_blocking"]


def _utc_now(value: datetime.datetime | None = None) -> datetime.datetime:
    value = value or datetime.datetime.now(datetime.UTC)
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.UTC)
    return value.astimezone(datetime.UTC)


def _unknown_budget(snapshot: dict) -> int:
    try:
        configured = max(1, int(os.getenv("API_QUOTA_UNKNOWN_RESERVE", "10")))
    except (TypeError, ValueError):
        configured = 10
    remaining = snapshot.get("daily_remaining")
    try:
        if remaining is not None:
            return min(configured, max(0, int(remaining)))
    except (TypeError, ValueError):
        pass
    return configured


def reserve_call(
    snapshot: dict,
    priority: str,
    *,
    cost: int = 1,
    database_engine=None,
    now: datetime.datetime | None = None,
) -> dict:
    """Atomically consume a local shared budget token, or reject the request.

    The ledger is process-shared only when workers use the same SQLite file.
    Remote Cloud/Actions quota arbitration remains a separate coordination layer.
    """
    cost = max(1, int(cost))
    snapshot = dict(snapshot or {})
    database_engine = database_engine or engine
    if database_engine.dialect.name != "sqlite":
        raise RuntimeError("Quota atomique uniquement pris en charge par SQLite.")
    current = _utc_now(now)
    day_key = current.date().isoformat()
    timestamp = current.isoformat()
    remaining_raw = snapshot.get("daily_remaining")
    limit_raw = snapshot.get("daily_limit")
    known = bool(snapshot.get("known")) and remaining_raw is not None and limit_raw is not None
    try:
        observed_remaining = max(0, int(remaining_raw)) if remaining_raw is not None else None
    except (TypeError, ValueError):
        observed_remaining = None
    try:
        daily_limit = max(0, int(limit_raw)) if limit_raw is not None else None
    except (TypeError, ValueError):
        daily_limit = None
    initial_remaining = (
        observed_remaining
        if known and observed_remaining is not None
        else _unknown_budget(snapshot)
    )
    reserve = api_quota_policy.reserve_for(snapshot)
    score = api_quota_policy.PRIORITIES.get(priority, 0)
    urgent = score >= _URGENT_PRIORITY

    connection = database_engine.connect()
    try:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        connection.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS {_TABLE} (
                    quota_day TEXT PRIMARY KEY,
                    remaining INTEGER NOT NULL,
                    known INTEGER NOT NULL DEFAULT 0,
                    daily_limit INTEGER,
                    updated_at TEXT NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(f"DELETE FROM {_TABLE} WHERE quota_day < :day"),
            {"day": day_key},
        )
        connection.execute(
            text(
                f"""
                INSERT OR IGNORE INTO {_TABLE} (
                    quota_day, remaining, known, daily_limit, updated_at
                ) VALUES (:day, :remaining, :known, :limit, :updated_at)
                """
            ),
            {
                "day": day_key,
                "remaining": initial_remaining,
                "known": int(known),
                "limit": daily_limit,
                "updated_at": timestamp,
            },
        )
        if observed_remaining is not None:
            connection.execute(
                text(
                    f"""
                    UPDATE {_TABLE}
                    SET remaining = MIN(remaining, :observed),
                        known = MAX(known, :known),
                        daily_limit = COALESCE(:limit, daily_limit),
                        updated_at = :updated_at
                    WHERE quota_day = :day
                    """
                ),
                {
                    "day": day_key,
                    "observed": observed_remaining,
                    "known": int(known),
                    "limit": daily_limit,
                    "updated_at": timestamp,
                },
            )
        row = connection.execute(
            text(f"SELECT remaining, known, daily_limit FROM {_TABLE} WHERE quota_day = :day"),
            {"day": day_key},
        ).mappings().one()
        available = max(0, int(row["remaining"] or 0))
        stored_known = bool(row["known"])
        if not stored_known and not urgent:
            result = {
                "allowed": False,
                "reason": "atomic_unknown_quota_urgent_only",
                "remaining": available,
                "reserve": reserve,
                "known": False,
            }
        elif available < cost:
            result = {
                "allowed": False,
                "reason": "atomic_budget_exhausted",
                "remaining": available,
                "reserve": reserve,
                "known": stored_known,
            }
        elif not urgent and available - cost < reserve:
            result = {
                "allowed": False,
                "reason": "atomic_emergency_reserve_protected",
                "remaining": available,
                "reserve": reserve,
                "known": stored_known,
            }
        else:
            updated = available - cost
            connection.execute(
                text(f"UPDATE {_TABLE} SET remaining = :remaining, updated_at = :updated_at WHERE quota_day = :day"),
                {"day": day_key, "remaining": updated, "updated_at": timestamp},
            )
            result = {
                "allowed": True,
                "reason": "reserved",
                "remaining": updated,
                "reserve": reserve,
                "known": stored_known,
            }
        connection.commit()
        return result
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
