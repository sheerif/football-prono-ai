from services import import_service


def test_manual_import_stops_at_safe_season_checkpoint(monkeypatch):
    class FakeSession:
        closed = False
        def close(self):
            self.closed = True
    session = FakeSession()
    monkeypatch.setattr(import_service, "SessionLocal", lambda: session)
    monkeypatch.setattr(import_service, "register_league_seasons", lambda *args, **kwargs: None)
    result = import_service.import_leagues_cautious(
        [61],
        seasons=[2026],
        priority_preemption_check=lambda: True,
    )
    assert result == {
        "preempted": True,
        "checkpoint": "core:61:2026",
        "league_id": 61,
        "season": 2026,
    }
    assert session.closed



def test_manual_import_priority_request_captures_resume_arguments(monkeypatch):
    from services import background_jobs
    import threading

    monkeypatch.setattr(background_jobs, "_jobs", {})
    monkeypatch.setattr(background_jobs, "_backup_started_jobs", set())
    monkeypatch.setattr(background_jobs, "_priority_preemption_requested", threading.Event())
    monkeypatch.setattr(background_jobs, "_pending_priority_prediction_days", None)
    monkeypatch.setattr(background_jobs, "_priority_prediction_pending", False)
    monkeypatch.setattr(background_jobs, "_priority_secondary_resume", None)
    details = {
        "league_ids": [61],
        "seasons": [2025, 2026],
        "pause": 0.2,
        "max_retries": 2,
        "selected_presets": ["core"],
    }
    job_id = background_jobs._create_job("manual_import", "manual", details)
    reused_id, created = background_jobs._create_unique_data_job(
        "prediction_sync", "priority", {"days": 1}
    )
    assert reused_id == job_id and not created
    assert background_jobs._priority_secondary_resume["kind"] == "manual_import"
    resumed = background_jobs._priority_secondary_resume["details"]
    assert resumed["league_ids"] == [61]
    assert resumed["seasons"] == [2025, 2026]
    assert resumed["pause"] == 0.2
    assert resumed["max_retries"] == 2
    assert resumed["selected_presets"] == ["core"]


def test_startup_updates_priority_request_captures_connection_context(monkeypatch):
    from services import background_jobs
    import threading

    monkeypatch.setattr(background_jobs, "_jobs", {})
    monkeypatch.setattr(background_jobs, "_backup_started_jobs", set())
    monkeypatch.setattr(background_jobs, "_priority_preemption_requested", threading.Event())
    monkeypatch.setattr(background_jobs, "_pending_priority_prediction_days", None)
    monkeypatch.setattr(background_jobs, "_priority_prediction_pending", False)
    monkeypatch.setattr(background_jobs, "_priority_secondary_resume", None)
    job_id = background_jobs._create_job(
        "startup_updates", "startup", {"connection_log_id": 42}
    )
    reused_id, created = background_jobs._create_unique_data_job(
        "prediction_sync", "priority", {"days": 1}
    )
    assert reused_id == job_id and not created
    assert background_jobs._priority_secondary_resume == {
        "kind": "startup_updates",
        "details": {"connection_log_id": 42, "priority_prediction_pending": True},
    }


def test_priority_prediction_completion_dispatches_manual_import_resume(monkeypatch):
    from services import background_jobs
    import threading

    monkeypatch.setattr(background_jobs, "_jobs", {})
    monkeypatch.setattr(background_jobs, "_backup_started_jobs", set())
    monkeypatch.setattr(background_jobs, "_priority_secondary_resume", {
        "kind": "manual_import",
        "details": {
            "league_ids": [61],
            "seasons": [2026],
            "pause": 0.5,
            "max_retries": 3,
            "selected_presets": ["core"],
        },
    })
    threads = []
    class FakeThread:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            threads.append(self)
        def start(self):
            pass
    monkeypatch.setattr(threading, "Thread", FakeThread)
    calls = []
    monkeypatch.setattr(background_jobs, "start_manual_import", lambda **kwargs: calls.append(kwargs))
    job_id = background_jobs._create_job("prediction_sync", "priority")
    background_jobs._set_job(job_id, status="done")
    resume_thread = next(t for t in threads if t.kwargs["name"] == "football-prono-resume-manual-import")
    resume_thread.kwargs["target"]()
    assert calls == [{
        "league_ids": [61],
        "seasons": [2026],
        "pause": 0.5,
        "max_retries": 3,
        "selected_presets": ["core"],
    }]


def test_priority_prediction_completion_restarts_startup_updates(monkeypatch):
    from services import background_jobs
    import threading

    monkeypatch.setattr(background_jobs, "_jobs", {})
    monkeypatch.setattr(background_jobs, "_backup_started_jobs", set())
    monkeypatch.setattr(background_jobs, "_priority_secondary_resume", {
        "kind": "startup_updates",
        "details": {"connection_log_id": 42},
    })
    monkeypatch.setattr(background_jobs, "_startup_started", True)
    threads = []
    class FakeThread:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            threads.append(self)
        def start(self):
            pass
    monkeypatch.setattr(threading, "Thread", FakeThread)
    calls = []
    monkeypatch.setattr(
        background_jobs,
        "start_startup_updates_once",
        lambda connection_log_id=None: calls.append(connection_log_id),
    )
    job_id = background_jobs._create_job("prediction_sync", "priority")
    background_jobs._set_job(job_id, status="done")
    resume_thread = next(t for t in threads if t.kwargs["name"] == "football-prono-resume-startup-updates")
    resume_thread.kwargs["target"]()
    assert calls == [42]
    assert background_jobs._startup_started is False



def test_current_competition_refresh_yields_before_first_league(monkeypatch):
    config = {
        "enabled": True,
        "current_enabled": True,
        "league_ids": [61],
        "end_season": 2026,
        "interval_minutes": 60,
        "pause": 0,
        "max_retries": 1,
    }
    class FakeSession:
        closed = False
        def close(self):
            self.closed = True
    session = FakeSession()
    monkeypatch.setattr(import_service, "get_auto_refresh_config", lambda: config)
    monkeypatch.setattr(import_service, "_get_sync_value", lambda key: None)
    monkeypatch.setattr(import_service, "register_league_seasons", lambda *args, **kwargs: None)
    monkeypatch.setattr(import_service, "SessionLocal", lambda: session)
    monkeypatch.setattr(import_service, "_active_season_for_league", lambda *args: 2026)
    result = import_service.refresh_current_competitions_on_connection(
        priority_preemption_check=lambda: True,
    )
    assert result["preempted"] is True
    assert result["checkpoint"] == "current-refresh:61:2026"
    assert result["refreshed"] == []
    assert session.closed


def test_active_season_refresh_yields_before_metadata_api_call():
    class FakeSession:
        def get(self, *args, **kwargs):
            raise AssertionError("priority checkpoint should precede DB/API work")
    result = import_service._refresh_league_season(
        FakeSession(),
        61,
        2026,
        pause=0,
        max_retries=1,
        priority_preemption_check=lambda: True,
    )
    assert result == {
        "preempted": True,
        "checkpoint": "league-metadata:61:2026",
    }
