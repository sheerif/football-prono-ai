"""SQLite atomic API quota reservation tests."""
from __future__ import annotations

import multiprocessing
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import create_engine

from services.api_quota_ledger import reserve_call


def _process_reserve(db_url: str, snapshot: dict, priority: str, queue) -> None:
    worker_engine = create_engine(
        db_url,
        connect_args={"timeout": 30},
        pool_pre_ping=True,
    )
    try:
        queue.put(reserve_call(snapshot, priority, database_engine=worker_engine)["allowed"])
    finally:
        worker_engine.dispose()


def test_concurrent_processes_cannot_spend_the_same_quota_tokens():
    with tempfile.TemporaryDirectory() as directory:
        db_path = os.path.join(directory, "quota.db")
        db_url = f"sqlite:///{db_path}"
        snapshot = {
            "daily_remaining": 20,
            "daily_limit": 100,
            "minute_remaining": 100,
            "daily_ratio": 0.2,
            "known": True,
        }
        ctx = multiprocessing.get_context("spawn")
        queue = ctx.Queue()
        processes = [
            ctx.Process(target=_process_reserve, args=(db_url, snapshot, "historical_backfill", queue))
            for _ in range(12)
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(30)
        for process in processes:
            assert process.exitcode == 0
        allowed = [queue.get(timeout=5) for _ in processes]
        # The policy reserves 15 tokens of the 100-call daily allowance.
        # At 20 remaining, only five historical calls may be atomically granted.
        assert sum(allowed) == 5
        queue.close()
        queue.join_thread()


def test_reservations_are_monotonic_with_stale_status_snapshots():
    with tempfile.TemporaryDirectory() as directory:
        engine = create_engine(f"sqlite:///{os.path.join(directory, 'quota.db')}")
        snapshot = {
            "daily_remaining": 100,
            "daily_limit": 100,
            "minute_remaining": 100,
            "daily_ratio": 1.0,
            "known": True,
        }
        first = reserve_call(snapshot, "live", database_engine=engine)
        stale = reserve_call(snapshot, "live", database_engine=engine)
        assert first["remaining"] == 99
        assert stale["remaining"] == 98
        engine.dispose()


def test_quota_ledger_resets_at_utc_day_boundary():
    with tempfile.TemporaryDirectory() as directory:
        engine = create_engine(f"sqlite:///{os.path.join(directory, 'quota.db')}")
        snapshot = {
            "daily_remaining": 10,
            "daily_limit": 100,
            "minute_remaining": 100,
            "daily_ratio": 0.1,
            "known": True,
        }
        before = reserve_call(
            snapshot,
            "live",
            database_engine=engine,
            now=datetime.datetime(2026, 11, 22, 23, 59, tzinfo=datetime.UTC),
        )
        after = reserve_call(
            snapshot,
            "live",
            database_engine=engine,
            now=datetime.datetime(2026, 11, 23, 0, 1, tzinfo=datetime.UTC),
        )
        assert before["remaining"] == 9
        assert after["remaining"] == 9
        engine.dispose()


def test_unknown_quota_allows_only_a_small_urgent_budget(monkeypatch):
    monkeypatch.setenv("API_QUOTA_UNKNOWN_RESERVE", "3")
    with tempfile.TemporaryDirectory() as directory:
        engine = create_engine(f"sqlite:///{os.path.join(directory, 'quota.db')}")
        snapshot = {
            "daily_remaining": None,
            "daily_limit": None,
            "minute_remaining": None,
            "daily_ratio": None,
            "known": False,
        }
        allowed = [
            reserve_call(snapshot, "live", database_engine=engine)["allowed"]
            for _ in range(4)
        ]
        secondary = reserve_call(snapshot, "historical_backfill", database_engine=engine)
        assert allowed == [True, True, True, False]
        assert secondary["allowed"] is False
        engine.dispose()
