"""Atomic daily API-Football quota reservations.

SQLite serializes local consumers. When GitHub coordination credentials are
available, the remote allocator additionally hands out conservative quota
blocks shared by Streamlit Cloud and Actions. Remote blocks are owner-scoped
and never reused from a restored database by a different runtime.
"""
from __future__ import annotations

import datetime
import os

from sqlalchemy import text

from database.database import engine
from services import api_quota_policy, api_quota_remote

_TABLE = "api_quota_ledger"
_REMOTE_TABLE = "api_quota_remote_allocations"
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
    remote_owner: str | None = None,
) -> dict:
    """Atomically reserve a local token and, when enabled, a remote shared token."""
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
    remote_enabled = api_quota_remote.enabled()
    owner = remote_owner or api_quota_remote.allocation_owner()
    priority_class = "urgent" if urgent else "normal"

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
        if remote_enabled:
            connection.execute(
                text(
                    f"""
                    CREATE TABLE IF NOT EXISTS {_REMOTE_TABLE} (
                        quota_day TEXT NOT NULL,
                        allocation_id TEXT NOT NULL,
                        allocation_owner TEXT NOT NULL,
                        priority_class TEXT NOT NULL,
                        remaining INTEGER NOT NULL,
                        remote_reported INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        PRIMARY KEY (quota_day, allocation_id)
                    )
                    """
                )
            )
        connection.execute(
            text(f"DELETE FROM {_TABLE} WHERE quota_day < :day"),
            {"day": day_key},
        )
        if remote_enabled:
            connection.execute(
                text(f"DELETE FROM {_REMOTE_TABLE} WHERE quota_day < :day"),
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
                "remote_enabled": remote_enabled,
                "remote_shared": False,
            }
        elif available < cost:
            result = {
                "allowed": False,
                "reason": "atomic_budget_exhausted",
                "remaining": available,
                "reserve": reserve,
                "known": stored_known,
                "remote_enabled": remote_enabled,
                "remote_shared": False,
            }
        elif not urgent and available - cost < reserve:
            result = {
                "allowed": False,
                "reason": "atomic_emergency_reserve_protected",
                "remaining": available,
                "reserve": reserve,
                "known": stored_known,
                "remote_enabled": remote_enabled,
                "remote_shared": False,
            }
        else:
            remote_shared = False
            allocation_row = None
            if remote_enabled:
                allocation_row = connection.execute(
                    text(
                        f"""
                        SELECT allocation_id, remaining
                        FROM {_REMOTE_TABLE}
                        WHERE quota_day = :day
                          AND allocation_owner = :owner
                          AND priority_class = :priority_class
                          AND remaining >= :cost
                        ORDER BY created_at
                        LIMIT 1
                        """
                    ),
                    {
                        "day": day_key,
                        "owner": owner,
                        "priority_class": priority_class,
                        "cost": cost,
                    },
                ).mappings().first()
                if allocation_row is None:
                    pending = connection.execute(
                        text(
                            f"""
                            SELECT allocation_id FROM {_REMOTE_TABLE}
                            WHERE quota_day = :day
                              AND allocation_owner = :owner
                              AND remaining = 0
                              AND remote_reported = 0
                            """
                        ),
                        {"day": day_key, "owner": owner},
                    ).mappings().all()
                    pending_ids = [item["allocation_id"] for item in pending]
                    remote_block = api_quota_remote.reserve_block(
                        snapshot,
                        priority,
                        spent_allocation_ids=pending_ids,
                        cost=cost,
                        now=current,
                    )
                    if remote_block.get("spent_reported"):
                        connection.execute(
                            text(
                                f"""
                                UPDATE {_REMOTE_TABLE}
                                SET remote_reported = 1
                                WHERE quota_day = :day
                                  AND allocation_owner = :owner
                                  AND remaining = 0
                                """
                            ),
                            {"day": day_key, "owner": owner},
                        )
                    if not remote_block.get("allowed"):
                        result = {
                            "allowed": False,
                            "reason": remote_block.get("reason", "remote_shared_quota_denied"),
                            "remaining": available,
                            "reserve": reserve,
                            "known": stored_known,
                            "remote_enabled": True,
                            "remote_shared": False,
                        }
                        connection.commit()
                        return result
                    connection.execute(
                        text(
                            f"""
                            INSERT INTO {_REMOTE_TABLE} (
                                quota_day, allocation_id, allocation_owner,
                                priority_class, remaining, remote_reported, created_at
                            ) VALUES (
                                :day, :allocation_id, :owner, :priority_class,
                                :remaining, 0, :created_at
                            )
                            """
                        ),
                        {
                            "day": day_key,
                            "allocation_id": remote_block["allocation_id"],
                            "owner": owner,
                            "priority_class": priority_class,
                            "remaining": int(remote_block["amount"]),
                            "created_at": timestamp,
                        },
                    )
                    allocation_row = {
                        "allocation_id": remote_block["allocation_id"],
                        "remaining": int(remote_block["amount"]),
                    }
                if allocation_row is not None:
                    remote_remaining = int(allocation_row["remaining"]) - cost
                    connection.execute(
                        text(
                            f"""
                            UPDATE {_REMOTE_TABLE}
                            SET remaining = :remaining
                            WHERE quota_day = :day AND allocation_id = :allocation_id
                            """
                        ),
                        {
                            "day": day_key,
                            "allocation_id": allocation_row["allocation_id"],
                            "remaining": remote_remaining,
                        },
                    )
                    remote_shared = True
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
                "remote_enabled": remote_enabled,
                "remote_shared": remote_shared,
                "remote_allocation_id": (
                    allocation_row["allocation_id"] if remote_shared else None
                ),
            }
        connection.commit()
        return result
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
