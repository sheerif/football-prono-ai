"""Autonomous near-real-time monitor for live and imminent fixtures.

API-Football has no push events: this process-local worker polls with adaptive
quota-aware cadence, shared request caching, and a best-effort prediction refresh.
It is not an always-on scheduler and production must keep the app instance alive.
"""
import datetime
import logging
import threading

from services import import_service

logger = logging.getLogger(__name__)
_lock = threading.Lock()
_started = False
_stop = threading.Event()
_last_signature = None


def _interval_seconds(*, has_live: bool, has_imminent: bool = False, quota: dict | None = None) -> int:
    from services import api_quota_policy

    snapshot = api_quota_policy.quota_snapshot(quota)
    return api_quota_policy.suggested_poll_seconds(
        live=has_live,
        imminent=has_imminent,
        snapshot=snapshot,
    )


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
            fixture.get("date"),
            (teams.get("home") or {}).get("id"),
            (teams.get("away") or {}).get("id"),
        ))
    return tuple(sorted(signature))


def _kickoff(item: dict) -> datetime.datetime | None:
    raw = ((item.get("fixture") or {}).get("date") or "").strip()
    if not raw:
        return None
    try:
        value = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=datetime.UTC)
        return value.astimezone(datetime.UTC)
    except (TypeError, ValueError):
        return None


def _filter_imminent(items: list[dict], *, now: datetime.datetime | None = None) -> list[dict]:
    now = now or datetime.datetime.now(datetime.UTC)
    horizon = now + datetime.timedelta(hours=24)
    excluded_statuses = {"1H", "HT", "2H", "ET", "BT", "P", "LIVE", "INT", "SUSP", "FT", "AET", "PEN", "CANC", "ABD", "AWD", "WO"}
    imminent = []
    for item in items:
        fixture = item.get("fixture") or {}
        fixture_id = fixture.get("id") or item.get("id")
        status = str(((fixture.get("status") or {}).get("short") or "")).upper()
        kickoff = _kickoff(item)
        if status in excluded_statuses or not kickoff:
            continue
        if now <= kickoff <= horizon and fixture_id:
            imminent.append(item)
    return imminent


def _persist_live_fixtures(items: list[dict]) -> int:
    """Persist live and upcoming fixtures through shared import normalizers."""
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


def poll_once(client=None, *, now: datetime.datetime | None = None, persist: bool = True) -> dict:
    """Poll live plus next-24h fixtures; optionally defer writes during another job."""
    client = client or import_service.client
    now = now or datetime.datetime.now(datetime.UTC)
    live_payload = client._get("/fixtures", {"live": "all"})
    live_errors = live_payload.get("errors") if isinstance(live_payload, dict) else None
    if live_errors:
        raise RuntimeError(f"API live fixtures returned errors: {live_errors}")
    live_items = live_payload.get("response") or []

    from_date = now.date().isoformat()
    to_date = (now + datetime.timedelta(hours=24)).date().isoformat()
    imminent_payload = client._get("/fixtures", {"from": from_date, "to": to_date})
    imminent_errors = imminent_payload.get("errors") if isinstance(imminent_payload, dict) else None
    if imminent_errors:
        raise RuntimeError(f"API imminent fixtures returned errors: {imminent_errors}")
    imminent_items = _filter_imminent(imminent_payload.get("response") or [], now=now)
    live_ids = {int((item.get("fixture") or {}).get("id") or item.get("id") or 0) for item in live_items}
    imminent_items = [
        item for item in imminent_items
        if int((item.get("fixture") or {}).get("id") or item.get("id") or 0) not in live_ids
    ]
    items = live_items + imminent_items
    changed = _persist_live_fixtures(items) if persist else 0
    return {
        "live_count": len(live_items),
        "imminent_count": len(imminent_items),
        "changed": changed,
        "signature": _fixture_signature(items),
        "quota": dict(getattr(client, "last_rate_limit", {}) or {}),
    }


def _failure_delay(error_count: int, quota: dict | None = None) -> int:
    delay = min(300 * (2 ** max(0, int(error_count) - 1)), 1800)
    retry_after = (quota or {}).get("retry_after")
    try:
        delay = max(delay, min(1800, int(retry_after)))
    except (TypeError, ValueError):
        pass
    return delay


def _worker():
    global _last_signature
    client = import_service.client
    failures = 0
    while not _stop.is_set():
        try:
            from services import background_jobs
            job_active = background_jobs.data_job_running()
            # Keep monitoring priority fixtures while secondary data jobs run,
            # but defer SQLite writes until the active job reaches a checkpoint.
            result = poll_once(client, persist=not job_active)
            failures = 0
            signature = result["signature"]
            if signature != _last_signature and (
                _last_signature is not None
                or result["changed"] > 0
                or result["live_count"] > 0
                or result["imminent_count"] > 0
            ):
                try:
                    background_jobs.start_prediction_sync(days=1)
                except Exception:
                    logger.exception("Could not enqueue priority prediction refresh")
            if not job_active:
                _last_signature = signature
            delay = _interval_seconds(
                has_live=bool(result["live_count"]),
                has_imminent=bool(result["imminent_count"]),
                quota=result["quota"],
            )
        except Exception:
            failures += 1
            logger.exception("Live/imminent priority polling failed")
            delay = _failure_delay(failures, getattr(client, "last_rate_limit", {}))
        _stop.wait(delay)


def start():
    """Start at most one daemon watcher in this process when API credentials exist."""
    global _started
    if not getattr(import_service.client, "api_key", ""):
        logger.info("Live priority watcher not started: API key is not configured")
        return False
    with _lock:
        if _started:
            return False
        _started = True
    thread = threading.Thread(
        target=_worker, name="football-live-priority-watcher", daemon=True
    )
    try:
        thread.start()
    except Exception:
        with _lock:
            _started = False
        raise
    return True
