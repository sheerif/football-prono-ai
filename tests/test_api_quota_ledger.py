"""SQLite atomic API quota reservation tests."""
from __future__ import annotations

import datetime
import multiprocessing
import os
import tempfile
from unittest.mock import patch

from sqlalchemy import create_engine, text

from services.api_quota_ledger import reserve_call
from services import api_quota_remote


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


def test_concurrent_processes_cannot_spend_the_same_quota_tokens(monkeypatch):
    monkeypatch.setenv("API_QUOTA_REMOTE_RESERVATIONS", "0")
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
            if process.is_alive():
                process.terminate()
                process.join()
            assert process.exitcode == 0
        allowed = [queue.get(timeout=5) for _ in processes]
        # The policy reserves 15 tokens of the 100-call daily allowance.
        # At 20 remaining, only five historical calls may be atomically granted.
        assert sum(allowed) == 5
        queue.close()
        queue.join_thread()


def test_reservations_are_monotonic_with_stale_status_snapshots(monkeypatch):
    monkeypatch.setenv("API_QUOTA_REMOTE_RESERVATIONS", "0")
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


def test_quota_ledger_resets_at_utc_day_boundary(monkeypatch):
    monkeypatch.setenv("API_QUOTA_REMOTE_RESERVATIONS", "0")
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
    monkeypatch.setenv("API_QUOTA_REMOTE_RESERVATIONS", "0")
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


def test_remote_blocks_are_consumed_and_not_reused_by_another_runtime(monkeypatch):
    monkeypatch.setenv("API_QUOTA_REMOTE_RESERVATIONS", "1")
    with tempfile.TemporaryDirectory() as directory:
        engine = create_engine(f"sqlite:///{os.path.join(directory, 'quota.db')}")
        snapshot = {
            "daily_remaining": 100,
            "daily_limit": 100,
            "minute_remaining": 100,
            "daily_ratio": 1.0,
            "known": True,
        }
        grants = [
            {"allowed": True, "allocation_id": "cloud-block", "amount": 1, "spent_reported": True},
            {"allowed": True, "allocation_id": "actions-block", "amount": 1, "spent_reported": True},
        ]
        with patch.object(api_quota_remote, "enabled", return_value=True), patch.object(
            api_quota_remote, "reserve_block", side_effect=grants
        ) as reserve_remote:
            cloud = reserve_call(
                snapshot, "live", database_engine=engine, remote_owner="cloud-runtime"
            )
            actions = reserve_call(
                snapshot, "live", database_engine=engine, remote_owner="actions-run-123"
            )
        assert cloud["allowed"] and cloud["remote_shared"]
        assert actions["allowed"] and actions["remote_shared"]
        assert reserve_remote.call_count == 2
        assert cloud["remote_allocation_id"] == "cloud-block"
        assert actions["remote_allocation_id"] == "actions-block"
        engine.dispose()


def test_remote_block_exhaustion_does_not_consume_local_token(monkeypatch):
    monkeypatch.setenv("API_QUOTA_REMOTE_RESERVATIONS", "1")
    with tempfile.TemporaryDirectory() as directory:
        engine = create_engine(f"sqlite:///{os.path.join(directory, 'quota.db')}")
        snapshot = {
            "daily_remaining": 100,
            "daily_limit": 100,
            "minute_remaining": 100,
            "daily_ratio": 1.0,
            "known": True,
        }
        grants = [
            {"allowed": True, "allocation_id": "block-1", "amount": 2, "spent_reported": True},
            {"allowed": False, "reason": "remote_shared_quota_exhausted", "spent_reported": True},
        ]
        with patch.object(api_quota_remote, "enabled", return_value=True), patch.object(
            api_quota_remote, "reserve_block", side_effect=grants
        ) as reserve_remote:
            first = reserve_call(
                snapshot, "historical_backfill", database_engine=engine, remote_owner="cloud"
            )
            second = reserve_call(
                snapshot, "historical_backfill", database_engine=engine, remote_owner="cloud"
            )
            third = reserve_call(
                snapshot, "historical_backfill", database_engine=engine, remote_owner="cloud"
            )
        assert [first["allowed"], second["allowed"], third["allowed"]] == [True, True, False]
        assert first["remote_shared"] and second["remote_shared"]
        assert third["reason"] == "remote_shared_quota_exhausted"
        assert reserve_remote.call_count == 2
        pending = reserve_remote.call_args_list[1].kwargs["spent_allocation_ids"]
        assert pending == ["block-1"]
        ledger = engine.connect()
        try:
            remaining = ledger.execute(
                text("SELECT remaining FROM api_quota_ledger WHERE quota_day = :day"),
                {"day": datetime.datetime.now(datetime.UTC).date().isoformat()},
            ).scalar_one()
            assert remaining == 98
        finally:
            ledger.close()
        engine.dispose()
