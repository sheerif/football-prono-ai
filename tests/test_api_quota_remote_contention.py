"""Regression tests for transient remote quota allocation lock contention."""
from unittest.mock import patch

from services import api_quota_remote


def test_allocation_lock_retries_transient_contention_then_succeeds():
    with patch.object(
        api_quota_remote.remote_sync_lease,
        "claim",
        side_effect=[False, False, True],
    ) as claim, patch.object(api_quota_remote.time, "sleep") as sleep:
        assert api_quota_remote._claim_allocation_lock(
            "api-quota-ledger:2026-11-24", "quota-test"
        ) is True

    assert claim.call_count == 3
    assert [call.args[0] for call in sleep.call_args_list] == [0.2, 0.4]


def test_allocation_lock_fails_closed_after_bounded_retries():
    with patch.object(
        api_quota_remote.remote_sync_lease,
        "claim",
        return_value=False,
    ) as claim, patch.object(api_quota_remote.time, "sleep") as sleep:
        assert api_quota_remote._claim_allocation_lock(
            "api-quota-ledger:2026-11-24", "quota-test"
        ) is False

    assert claim.call_count == api_quota_remote._LOCK_ACQUIRE_ATTEMPTS
    assert sleep.call_count == api_quota_remote._LOCK_ACQUIRE_ATTEMPTS - 1
