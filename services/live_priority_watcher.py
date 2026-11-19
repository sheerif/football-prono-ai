"""Autonomous near-real-time monitor for live football fixtures.

API-Football does not push events into this application, so this service polls
its live-fixtures endpoint with quota-aware cadence and persists changed scores
and statuses. It is a best-effort process-local worker, not a guaranteed
always-on scheduler; production hosting must keep the app process alive.
"""
import datetime
import logging
import os
import threading
import time

from services import import_service

logger = logging.getLogger(__name__)
_lock = threading.Lock()
_started = False
_stop = threading.Event()
_last_signature = None


def _interval_seconds(*, has_live: bool, quota: dict | None = None) -> int:
    """Adaptive polling cadence; reserve quota and slow down when it is low."""
    quota = quota or {}
    try:
        configured = max(30, int(os.getenv("LIVE_PRIORITY_POLL_SECONDS", "120")))
    except (TypeError, ValueError):
        configured = 120
    remaining = quota.get("daily_remaining")
    minute_remaining = quota.get("minute_remaining")
    try:
        if remaining is not None and int(remaining) < 1000:
            return max(configured, 600)
    except (TypeError, ValueError):
        pass
    try:
        if minute_remaining is not None and int(minute_remaining) <= 2:
            return max(configured, 300)
    except (TypeError, ValueError):
        pass
    return min(configured, 120) if has_live else max(configured, 900)


def _fixture_signature(items: list[dict]) -> tuple:
    signature = []
    for item in items:
        fixture = item.get("fixture") or {}
        teams = item.get("teams") or {}
        goals = item.get("goals") or {}
        status = fixture.get("status") or {}
        signature.append((
            int(fixture.get("id") or item.get("id") or 0),
            str(status.get("short") or status.get("long") or ""),
            goals.get("home"),
            goals.get("away"),
            (teams.get("home") or {}).get("id"),
            (teams.get("away") or {}).get("id"),
        ))
    return tuple(sorted(signature))


def _persist_live_fixtures(items: list[dict]) -> int:
    """Write live scores/status/details through the same normalizers as imports."""
    if not items:
        return 0
    import_service.init_db()
    from database import models
    from database.database import SessionLocal

    changed = 0
    session = SessionLocal()
    try:
        for item in items:
            fixture = item.get("fixture") or {}
            league = item.get("league") or {}
            fixture_id = fixture.get("id") or item.get("id")
            league_id = league.get("id")
            season = league.get("season")
            if not fixture_id or not league_id or not season:
                continue
            existing = session.get(models.Match, int(fixture_id))
            status_obj = fixture.get("status") or {}
            status = status_obj.get("long") or status_obj.get("short")
            goals = item.get("goals") or {}
            before = (
                existing.status, existing.home_goals, existing.away_goals
            ) if existing else None
            teams = item.get("teams") or {}
            for side in ("home", "away"):
                team = teams.get(side) or {}
                if team.get("id"):
                    import_service._get_or_create_team(
                        session, team, league_id=int(league_id)
                    )
            import_service._save_match(session, item, int(league_id), int(season))
            import_service._save_fixture_api_detail(
                session, item, int(league_id), int(season)
            )
            updated = session.get(models.Match, int(fixture_id))
            after = (
                updated.status, updated.home_goals, updated.away_goals
            ) if updated else None
            if before != after:
                changed += 1
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return changed


def poll_once(client=None) -> dict:
    """One API poll, suitable for tests and diagnostics."""
    client = client or import_service.client
    payload = client._get("/fixtures", {"live": "all"})
    errors = payload.get("errors") if isinstance(payload, dict) else None
    if errors:
        raise RuntimeError(f"API live fixtures returned errors: {errors}")
    items = payload.get("response") or []
    changed = _persist_live_fixtures(items)
    return {
        "live_count": len(items),
        "changed": changed,
        "signature": _fixture_signature(items),
        "quota": dict(getattr(client, "last_rate_limit", {}) or {}),
    }


def _worker():
    global _last_signature
    client = import_service.client
    while not _stop.is_set():
        try:
            result = poll_once(client)
            signature = result["signature"]
            # Trigger the existing prediction updater only when the live feed
            # changes, preventing repeated identical downloads.
            if signature != _last_signature and (_last_signature is not None or result["changed"] > 0):
                try:
                    from services import background_jobs
                    background_jobs.start_prediction_sync(days=3)
                except Exception:
                    logger.exception("Could not enqueue priority prediction refresh")
            _last_signature = signature
            delay = _interval_seconds(
                has_live=bool(result["live_count"]), quota=result["quota"]
            )
        except Exception:
            logger.exception("Live priority polling failed")
            delay = 300
        _stop.wait(delay)


def start():
    """Start exactly one daemon watcher in this application process."""
    global _started
    with _lock:
        if _started:
            return False
        _started = True
    thread = threading.Thread(
        target=_worker, name="football-live-priority-watcher", daemon=True
    )
    thread.start()
    return True
