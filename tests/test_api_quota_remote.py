"""Tests for the cross-environment API quota block allocator."""
from __future__ import annotations

import base64
import datetime
import json
import threading
from unittest.mock import patch

from services import api_quota_remote


class FakeGitHubContents:
    def __init__(self):
        self.record = None
        self.sha = None
        self.next_sha = 1
        self.conflict_next = False

    def request(self, method, endpoint, token, payload=None):
        if method == "GET":
            if self.record is None:
                raise RuntimeError("GitHub API (404) : missing")
            return {
                "sha": self.sha,
                "content": base64.b64encode(
                    json.dumps(self.record).encode("utf-8")
                ).decode("ascii"),
            }
        if method == "PUT":
            if self.conflict_next:
                self.conflict_next = False
                raise RuntimeError("GitHub API (422) : CAS conflict")
            if payload.get("sha") != self.sha:
                raise RuntimeError("GitHub API (409) : CAS conflict")
            self.record = json.loads(
                base64.b64decode(payload["content"]).decode("utf-8")
            )
            self.sha = f"blob-{self.next_sha}"
            self.next_sha += 1
            return {"content": {"sha": self.sha}}
        raise AssertionError(f"Unexpected request: {method} {endpoint}")


def _snapshot(remaining=20, limit=100):
    return {
        "daily_remaining": remaining,
        "daily_limit": limit,
        "minute_remaining": 100,
        "daily_ratio": remaining / limit,
        "known": True,
    }


class RemoteQuotaTests:
    def setup_method(self):
        self.github = FakeGitHubContents()
        self.lock = threading.Lock()
        self.claim_patch = patch.object(
            api_quota_remote.remote_sync_lease,
            "claim",
            side_effect=lambda *args, **kwargs: self.lock.acquire(blocking=False),
        )
        self.release_patch = patch.object(
            api_quota_remote.remote_sync_lease,
            "release",
            side_effect=lambda *args, **kwargs: (self.lock.release() or True),
        )
        self.repo_patch = patch.object(
            api_quota_remote.remote_sync_lease,
            "_repo_token",
            return_value=("sheerif/football-prono-ai", "test-token"),
        )
        self.branch_patch = patch.object(
            api_quota_remote.remote_sync_lease,
            "_branch",
            return_value="sync-coordination",
        )
        self.request_patch = patch.object(
            api_quota_remote.git_backup,
            "_request",
            side_effect=self.github.request,
        )
        self.enabled_patch = patch.object(api_quota_remote, "enabled", return_value=True)
        for p in (
            self.claim_patch,
            self.release_patch,
            self.repo_patch,
            self.branch_patch,
            self.request_patch,
            self.enabled_patch,
        ):
            p.start()

    def teardown_method(self):
        for p in (
            self.enabled_patch,
            self.request_patch,
            self.branch_patch,
            self.repo_patch,
            self.release_patch,
            self.claim_patch,
        ):
            p.stop()


def test_normal_and_urgent_allocations_respect_global_emergency_reserve():
    import os
    old = os.environ.get("API_QUOTA_REMOTE_BLOCK_SIZE")
    os.environ["API_QUOTA_REMOTE_BLOCK_SIZE"] = "10"
    try:
        test = RemoteQuotaTests()
        test.setup_method()
        try:
            snapshot = _snapshot(20, 100)
            normal = api_quota_remote.reserve_block(snapshot, "historical_backfill")
            denied_normal = api_quota_remote.reserve_block(snapshot, "historical_backfill")
            urgent1 = api_quota_remote.reserve_block(snapshot, "live")
            urgent2 = api_quota_remote.reserve_block(snapshot, "prediction_blocking")
            denied_urgent = api_quota_remote.reserve_block(snapshot, "live")
            assert normal["allowed"] and normal["amount"] == 5
            assert denied_normal["allowed"] is False
            assert urgent1["allowed"] and urgent1["amount"] == 10
            assert urgent2["allowed"] and urgent2["amount"] == 5
            assert denied_urgent["allowed"] is False
            assert sum(
                item["amount"]
                for item in (normal, urgent1, urgent2)
            ) == 20
        finally:
            test.teardown_method()
    finally:
        if old is None:
            os.environ.pop("API_QUOTA_REMOTE_BLOCK_SIZE", None)
        else:
            os.environ["API_QUOTA_REMOTE_BLOCK_SIZE"] = old


def test_unknown_quota_is_urgent_only_and_bounded():
    import os
    old_budget = os.environ.get("API_QUOTA_UNKNOWN_RESERVE")
    old_block = os.environ.get("API_QUOTA_REMOTE_BLOCK_SIZE")
    os.environ["API_QUOTA_UNKNOWN_RESERVE"] = "3"
    os.environ["API_QUOTA_REMOTE_BLOCK_SIZE"] = "10"
    try:
        test = RemoteQuotaTests()
        test.setup_method()
        try:
            unknown = {
                "daily_remaining": None,
                "daily_limit": None,
                "minute_remaining": None,
                "daily_ratio": None,
                "known": False,
            }
            normal = api_quota_remote.reserve_block(unknown, "historical_backfill")
            urgent = api_quota_remote.reserve_block(unknown, "live")
            another_urgent = api_quota_remote.reserve_block(
                unknown, "live", spent_allocation_ids=[urgent["allocation_id"]]
            )
            assert normal["allowed"] is False
            assert normal["reason"] == "remote_quota_unknown_urgent_only"
            assert urgent["allowed"] and urgent["amount"] == 3
            assert another_urgent["allowed"] is False
        finally:
            test.teardown_method()
    finally:
        if old_budget is None:
            os.environ.pop("API_QUOTA_UNKNOWN_RESERVE", None)
        else:
            os.environ["API_QUOTA_UNKNOWN_RESERVE"] = old_budget
        if old_block is None:
            os.environ.pop("API_QUOTA_REMOTE_BLOCK_SIZE", None)
        else:
            os.environ["API_QUOTA_REMOTE_BLOCK_SIZE"] = old_block


def test_stale_status_snapshot_cannot_increase_shared_pool():
    import os
    old = os.environ.get("API_QUOTA_REMOTE_BLOCK_SIZE")
    os.environ["API_QUOTA_REMOTE_BLOCK_SIZE"] = "10"
    try:
        test = RemoteQuotaTests()
        test.setup_method()
        try:
            first = api_quota_remote.reserve_block(_snapshot(100), "live")
            second = api_quota_remote.reserve_block(
                _snapshot(100), "live", spent_allocation_ids=[first["allocation_id"]]
            )
            third = api_quota_remote.reserve_block(
                _snapshot(80), "live", spent_allocation_ids=[second["allocation_id"]]
            )
            stale = api_quota_remote.reserve_block(
                _snapshot(100), "live", spent_allocation_ids=[third["allocation_id"]]
            )
            assert all(item["allowed"] for item in (first, second, third, stale))
            assert [item["amount"] for item in (first, second, third, stale)] == [10, 10, 10, 10]
            assert test.github.record["lowest_observed_remaining"] == 80
            assert test.github.record["available"] == 60
        finally:
            test.teardown_method()
    finally:
        if old is None:
            os.environ.pop("API_QUOTA_REMOTE_BLOCK_SIZE", None)
        else:
            os.environ["API_QUOTA_REMOTE_BLOCK_SIZE"] = old


def test_remote_cas_conflict_fails_closed():
    import os
    old = os.environ.get("API_QUOTA_REMOTE_BLOCK_SIZE")
    os.environ["API_QUOTA_REMOTE_BLOCK_SIZE"] = "5"
    try:
        test = RemoteQuotaTests()
        test.setup_method()
        try:
            test.github.conflict_next = True
            result = api_quota_remote.reserve_block(_snapshot(100), "live")
            assert result["allowed"] is False
            assert result["reason"] == "remote_shared_quota_cas_conflict"
        finally:
            test.teardown_method()
    finally:
        if old is None:
            os.environ.pop("API_QUOTA_REMOTE_BLOCK_SIZE", None)
        else:
            os.environ["API_QUOTA_REMOTE_BLOCK_SIZE"] = old


def test_concurrent_runtimes_cannot_allocate_the_same_shared_budget():
    import os
    old = os.environ.get("API_QUOTA_REMOTE_BLOCK_SIZE")
    os.environ["API_QUOTA_REMOTE_BLOCK_SIZE"] = "100"
    try:
        test = RemoteQuotaTests()
        test.setup_method()
        try:
            results = []
            workers = [
                threading.Thread(
                    target=lambda owner: results.append(
                        api_quota_remote.reserve_block(
                            _snapshot(20), "historical_backfill", now=datetime.datetime(
                                2026, 11, 23, 8, 0, tzinfo=datetime.UTC
                            )
                        )
                    ),
                    args=(f"worker-{index}",),
                )
                for index in range(2)
            ]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(10)
            assert len(results) == 2
            assert sum(int(result.get("amount") or 0) for result in results) == 5
            assert sum(bool(result["allowed"]) for result in results) == 1
        finally:
            test.teardown_method()
    finally:
        if old is None:
            os.environ.pop("API_QUOTA_REMOTE_BLOCK_SIZE", None)
        else:
            os.environ["API_QUOTA_REMOTE_BLOCK_SIZE"] = old
