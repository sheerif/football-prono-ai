"""Quota-aware API-Football call budgeting and priority policy.

The policy is deliberately conservative when quota headers are missing. It
reserves a configurable portion of the daily budget for live/imminent fixtures
and avoids spending the reserve on historical enrichment.
"""
import os


PRIORITIES = {
    "live": 100,
    "imminent": 90,
    "prediction_blocking": 85,
    "current_fixtures": 75,
    "standings": 65,
    "recent_results": 55,
    "lineups": 50,
    "player_stats": 30,
    "xg_enrichment": 20,
    "historical_backfill": 10,
}


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def quota_snapshot(headers: dict | None) -> dict:
    headers = headers or {}
    def number(key):
        try:
            return int(headers[key])
        except (KeyError, TypeError, ValueError):
            return None
    remaining = number("daily_remaining")
    limit = number("daily_limit")
    minute_remaining = number("minute_remaining")
    ratio = (remaining / limit) if remaining is not None and limit and limit > 0 else None
    return {
        "daily_remaining": remaining,
        "daily_limit": limit,
        "minute_remaining": minute_remaining,
        "daily_ratio": ratio,
        "known": remaining is not None and limit is not None and limit > 0,
    }


def reserve_for(snapshot: dict) -> int:
    """Reserve a dynamic emergency allowance, capped by remaining quota."""
    remaining = snapshot.get("daily_remaining")
    if remaining is None:
        return _env_int("API_QUOTA_UNKNOWN_RESERVE", 10, 1)
    limit = snapshot.get("daily_limit") or remaining
    percent = _env_int("API_QUOTA_LIVE_RESERVE_PERCENT", 15, 5)
    fixed = _env_int("API_QUOTA_LIVE_RESERVE_MIN", 10, 1)
    reserve = max(fixed, int(limit * percent / 100))
    return min(remaining, reserve)


def can_spend(snapshot: dict, priority: str, *, cost: int = 1) -> dict:
    """Return a reasoned decision; callers must use it before nonurgent work."""
    cost = max(1, int(cost))
    priority_score = PRIORITIES.get(priority, 0)
    remaining = snapshot.get("daily_remaining")
    minute_remaining = snapshot.get("minute_remaining")
    reserve = reserve_for(snapshot)
    if minute_remaining is not None and minute_remaining < cost:
        return {"allowed": False, "reason": "minute_quota", "reserve": reserve}
    if remaining is None:
        # Unknown quota: allow only operationally critical calls, not backfill.
        allowed = priority_score >= PRIORITIES["prediction_blocking"]
        return {"allowed": allowed, "reason": "quota_unknown" if not allowed else "critical_unknown_quota", "reserve": reserve}
    if remaining < cost:
        return {"allowed": False, "reason": "daily_quota_exhausted", "reserve": reserve}
    if remaining - cost < reserve and priority_score < PRIORITIES["prediction_blocking"]:
        return {"allowed": False, "reason": "emergency_reserve_protected", "reserve": reserve}
    return {"allowed": True, "reason": "within_budget", "reserve": reserve}


def suggested_poll_seconds(*, live: bool, imminent: bool, snapshot: dict) -> int:
    """Quota-adaptive cadence for the live watcher, with an emergency floor."""
    if live:
        base = _env_int("LIVE_PRIORITY_POLL_SECONDS", 60, 30)
    elif imminent:
        base = _env_int("IMMINENT_PRIORITY_POLL_SECONDS", 180, 60)
    else:
        base = _env_int("LIVE_IDLE_POLL_SECONDS", 900, 120)
    remaining = snapshot.get("daily_remaining")
    limit = snapshot.get("daily_limit")
    minute_remaining = snapshot.get("minute_remaining")
    ratio = (remaining / limit) if remaining is not None and limit and limit > 0 else None
    if minute_remaining is not None and minute_remaining <= 2:
        return max(base, 300)
    if ratio is not None and ratio <= 0.05:
        return max(base, 1800)
    if ratio is not None and ratio <= 0.15:
        return max(base, 600)
    if ratio is not None and ratio <= 0.30:
        return max(base, 300)
    return base
