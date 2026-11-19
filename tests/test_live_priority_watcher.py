import datetime
import threading

from services import live_priority_watcher


def test_polling_interval_is_fast_when_live():
    assert live_priority_watcher._interval_seconds(has_live=True, quota={}) <= 120


def test_polling_interval_slows_when_daily_quota_low():
    assert live_priority_watcher._interval_seconds(
        has_live=True, quota={"daily_remaining": 50, "daily_limit": 1000}
    ) >= 600


def test_live_fixture_signature_tracks_score_and_status():
    fixture = {
        "fixture": {"id": 123, "status": {"short": "1H"}},
        "teams": {"home": {"id": 1}, "away": {"id": 2}},
        "goals": {"home": 0, "away": 0},
    }
    changed_score = {
        **fixture,
        "goals": {"home": 1, "away": 0},
    }
    changed_status = {
        **fixture,
        "fixture": {"id": 123, "status": {"short": "HT"}},
    }
    assert live_priority_watcher._fixture_signature([fixture]) != (
        live_priority_watcher._fixture_signature([changed_score])
    )
    assert live_priority_watcher._fixture_signature([fixture]) != (
        live_priority_watcher._fixture_signature([changed_status])
    )


def test_poll_once_uses_live_endpoint_and_persists_changes(monkeypatch):
    class FakeClient:
        last_rate_limit = {"daily_remaining": 4000, "daily_limit": 7500}

        def _get(self, path, params):
            assert path == "/fixtures"
            if params == {"live": "all"}:
                return {"response": [{"fixture": {"id": 123, "status": {"short": "1H"}}}]}
            assert "from" in params and "to" in params
            return {"response": []}

    monkeypatch.setattr(live_priority_watcher, "_persist_live_fixtures", lambda items: 1)
    result = live_priority_watcher.poll_once(FakeClient())
    assert result["live_count"] == 1
    assert result["imminent_count"] == 0
    assert result["changed"] == 1


def test_imminent_cadence_is_faster_than_idle():
    assert live_priority_watcher._interval_seconds(has_live=False, has_imminent=True, quota={}) < live_priority_watcher._interval_seconds(has_live=False, has_imminent=False, quota={})


def test_filter_imminent_limits_to_next_24_hours_and_excludes_live():
    now = datetime.datetime.now(datetime.UTC)
    def item(fid, hours, status="NS"):
        return {"fixture": {"id": fid, "date": (now + datetime.timedelta(hours=hours)).isoformat(), "status": {"short": status}}}
    selected = live_priority_watcher._filter_imminent([item(1, 4), item(2, 30), item(3, 1, "1H")], now=now)
    assert [x["fixture"]["id"] for x in selected] == [1]


def test_poll_once_calls_live_and_imminent_endpoints_and_persists(monkeypatch):
    now = datetime.datetime.now(datetime.UTC)
    live = {"fixture": {"id": 10, "status": {"short": "1H"}}}
    imminent = {"fixture": {"id": 11, "date": (now + datetime.timedelta(hours=4)).isoformat(), "status": {"short": "NS"}}}
    saved = []
    class FakeClient:
        last_rate_limit = {"daily_remaining": 4000, "daily_limit": 7500}
        def _get(self, path, params):
            if params == {"live": "all"}:
                return {"response": [live]}
            assert path == "/fixtures"
            assert params["from"] == now.date().isoformat()
            return {"response": [imminent]}
    monkeypatch.setattr(live_priority_watcher, "_persist_live_fixtures", lambda items: saved.extend(items) or 2)
    result = live_priority_watcher.poll_once(FakeClient(), now=now)
    assert result["live_count"] == 1
    assert result["imminent_count"] == 1
    assert result["changed"] == 2
    assert {item["fixture"]["id"] for item in saved} == {10, 11}


def test_start_is_idempotent_and_skips_without_credentials(monkeypatch):
    monkeypatch.setattr(live_priority_watcher.import_service, "client", type("C", (), {"api_key": ""})())
    monkeypatch.setattr(live_priority_watcher, "_started", False)
    assert live_priority_watcher.start() is False
    monkeypatch.setattr(live_priority_watcher.import_service, "client", type("C", (), {"api_key": "test"})())
    import threading
    class FakeThread:
        def __init__(self, **kwargs): self.kwargs = kwargs; self.started = False
        def start(self): self.started = True
    fake = FakeThread()
    monkeypatch.setattr(threading, "Thread", lambda **kwargs: fake)
    assert live_priority_watcher.start() is True
    assert fake.started
    assert live_priority_watcher.start() is False
    monkeypatch.setattr(live_priority_watcher, "_started", False)


def test_priority_prediction_requests_signal_active_secondary_job(monkeypatch):
    from services import background_jobs
    monkeypatch.setattr(background_jobs, "_jobs", {})
    monkeypatch.setattr(background_jobs, "_backup_started_jobs", set())
    monkeypatch.setattr(background_jobs, "_priority_preemption_requested", threading.Event())
    monkeypatch.setattr(background_jobs, "_pending_priority_prediction_days", None)
    monkeypatch.setattr(background_jobs, "_priority_prediction_pending", False)
    monkeypatch.setattr(background_jobs, "_priority_secondary_resume", None)
    job_id = background_jobs._create_job("full_sync", "secondary", {"persistent": True})
    reused_id, created = background_jobs._create_unique_data_job("prediction_sync", "priority", {"days": 1})
    assert reused_id == job_id and not created
    assert background_jobs.priority_preemption_requested()
    assert background_jobs._priority_prediction_pending
    assert background_jobs._jobs[job_id]["details"]["priority_prediction_pending"]
    assert background_jobs._priority_secondary_resume == {"kind": "full_sync", "details": {"persistent": True, "priority_prediction_pending": True}}


def test_preemption_event_is_cleared_by_test_isolation(monkeypatch):
    from services import background_jobs
    monkeypatch.setattr(background_jobs, "_priority_preemption_requested", threading.Event())
    assert not background_jobs.priority_preemption_requested()


def test_watcher_backoff_grows_and_honors_retry_after():
    assert live_priority_watcher._failure_delay(1) == 300
    assert live_priority_watcher._failure_delay(3) == 1200
    assert live_priority_watcher._failure_delay(10) == 1800
    assert live_priority_watcher._failure_delay(1, {"retry_after": "900"}) == 900
