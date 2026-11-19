from services import api_quota_policy


def test_quota_snapshot_ratio():
    snap = api_quota_policy.quota_snapshot({
        "daily_remaining": "200",
        "daily_limit": "1000",
        "minute_remaining": "10",
    })
    assert snap["known"] is True
    assert snap["daily_ratio"] == 0.2


def test_reserve_protects_secondary_calls():
    snap = api_quota_policy.quota_snapshot({
        "daily_remaining": 100,
        "daily_limit": 1000,
        "minute_remaining": 20,
    })
    decision = api_quota_policy.can_spend(snap, "historical_backfill", cost=1)
    assert decision["allowed"] is False
    assert decision["reason"] == "emergency_reserve_protected"


def test_urgent_calls_can_use_reserve():
    snap = api_quota_policy.quota_snapshot({
        "daily_remaining": 20,
        "daily_limit": 1000,
        "minute_remaining": 20,
    })
    decision = api_quota_policy.can_spend(snap, "live", cost=1)
    assert decision["allowed"] is True


def test_unknown_quota_blocks_backfill_but_allows_urgent():
    snap = api_quota_policy.quota_snapshot({})
    assert not api_quota_policy.can_spend(snap, "historical_backfill")["allowed"]
    assert api_quota_policy.can_spend(snap, "prediction_blocking")["allowed"]


def test_poll_cadence_slows_as_quota_drops():
    high = {"daily_remaining": 800, "daily_limit": 1000, "minute_remaining": 30}
    low = {"daily_remaining": 50, "daily_limit": 1000, "minute_remaining": 30}
    assert api_quota_policy.suggested_poll_seconds(live=True, imminent=False, snapshot=high) < api_quota_policy.suggested_poll_seconds(live=True, imminent=False, snapshot=low)


def test_prediction_blocking_calls_can_use_the_reserve_but_secondary_calls_cannot():
    snap = api_quota_policy.quota_snapshot({
        "daily_remaining": 10,
        "daily_limit": 1000,
        "minute_remaining": 10,
    })
    assert api_quota_policy.can_spend(snap, "prediction_blocking")["allowed"]
    assert not api_quota_policy.can_spend(snap, "current_fixtures")["allowed"]
