from services import import_service


def test_manual_import_stops_at_safe_season_checkpoint(monkeypatch):
    from services import remote_sync_lease, sync_registry
    monkeypatch.setattr(remote_sync_lease, "claim", lambda *args, **kwargs: True)
    monkeypatch.setattr(remote_sync_lease, "renew", lambda *args, **kwargs: True)
    monkeypatch.setattr(remote_sync_lease, "release", lambda *args, **kwargs: True)
    monkeypatch.setattr(sync_registry, "mark", lambda *args, **kwargs: None)
    monkeypatch.setattr(sync_registry, "claim_download", lambda *args, **kwargs: True)
    monkeypatch.setattr(sync_registry, "renew_download_claim", lambda *args, **kwargs: True)
    monkeypatch.setattr(sync_registry, "release_download_claim", lambda *args, **kwargs: True)
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
            "resume_checkpoint": "fixtures-page:61:2026:3",
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
        "resume_checkpoint": "fixtures-page:61:2026:3",
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



def test_record_update_result_marks_priority_preemption(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        import_service,
        "record_update_log",
        lambda **kwargs: captured.update(kwargs) or 1,
    )
    import_service.record_update_result(
        "historique_auto",
        "2026-11-20T00:00:00",
        {"ran": True, "preempted": True, "checkpoint": "fixtures:61:2026"},
    )
    assert captured["status"] == "interrompue_priorité"



def test_preempted_manual_import_checkpoint_is_carried_to_resume(monkeypatch):
    from services import background_jobs
    import threading

    monkeypatch.setattr(background_jobs, "_jobs", {})
    monkeypatch.setattr(background_jobs, "_backup_started_jobs", set())
    monkeypatch.setattr(background_jobs, "_priority_preemption_requested", threading.Event())
    monkeypatch.setattr(background_jobs, "_pending_priority_prediction_days", None)
    monkeypatch.setattr(background_jobs, "_priority_prediction_pending", False)
    monkeypatch.setattr(background_jobs, "_priority_secondary_resume", None)
    monkeypatch.setattr(threading, "Thread", lambda **kwargs: type("T", (), {"start": lambda self: None})())
    job_id = background_jobs._create_job(
        "manual_import",
        "manual",
        {"league_ids": [61], "seasons": [2026], "pause": 0.1, "max_retries": 2},
    )
    prediction_id, created = background_jobs._create_unique_data_job(
        "prediction_sync", "priority", {"days": 1}
    )
    assert prediction_id == job_id and not created
    background_jobs._set_job(
        job_id,
        status="partial",
        details={"preempted": True, "checkpoint": "fixtures-page:61:2026:3"},
    )
    assert background_jobs._priority_secondary_resume["details"]["resume_checkpoint"] == (
        "fixtures-page:61:2026:3"
    )


def test_manual_import_resume_plan_targets_checkpoint_scope_and_page():
    plan = import_service._manual_import_resume_plan(
        [61, 62], [2024, 2025, 2026], "fixtures-page:62:2025:4"
    )
    assert plan == {
        "league_id": 62,
        "season": 2025,
        "league_index": 1,
        "season_index": 1,
        "phase": "fixtures-page",
        "page": 4,
    }


def test_manual_import_resume_plan_rejects_unknown_scope():
    assert import_service._manual_import_resume_plan(
        [61], [2026], "unknown:61:2026"
    ) is None



def test_manual_import_resumes_fixture_pagination_at_checkpoint_page(monkeypatch):
    from database import models
    from services import remote_sync_lease, sync_registry
    monkeypatch.setattr(remote_sync_lease, "claim", lambda *args, **kwargs: True)
    monkeypatch.setattr(remote_sync_lease, "renew", lambda *args, **kwargs: True)
    monkeypatch.setattr(remote_sync_lease, "release", lambda *args, **kwargs: True)
    monkeypatch.setattr(sync_registry, "mark", lambda *args, **kwargs: None)
    monkeypatch.setattr(sync_registry, "claim_download", lambda *args, **kwargs: True)
    monkeypatch.setattr(sync_registry, "renew_download_claim", lambda *args, **kwargs: True)
    monkeypatch.setattr(sync_registry, "release_download_claim", lambda *args, **kwargs: True)

    class Query:
        def __init__(self, model):
            self.model = model
        def filter_by(self, **kwargs):
            return self
        def count(self):
            if self.model is models.Team:
                return 4
            return 0

    class FakeSession:
        def query(self, model):
            return Query(model)
        def get(self, model, key):
            if model is models.League:
                return models.League(id=key, name="Test League", logo="logo")
            return None
        def commit(self):
            pass
        def rollback(self):
            pass
        def close(self):
            pass

    session = FakeSession()
    monkeypatch.setattr(import_service, "SessionLocal", lambda: session)
    monkeypatch.setattr(import_service, "register_league_seasons", lambda *args, **kwargs: None)
    monkeypatch.setattr(import_service, "_get_or_create_team", lambda *args, **kwargs: None)
    monkeypatch.setattr(import_service, "_save_match", lambda *args, **kwargs: None)
    monkeypatch.setattr(import_service, "_save_fixture_api_detail", lambda *args, **kwargs: None)
    requested = []

    class Client:
        def _get(self, path, params):
            requested.append((path, params))
            assert params == {"league": 61, "season": 2026, "page": 3}
            return {
                "response": [{"fixture": {"id": 3003}, "teams": {}}],
                "paging": {"current": 3, "total": 4},
            }
        def get_fixtures(self, *args, **kwargs):
            raise AssertionError("resume must not restart from page 1")
        def get_standings(self, *args, **kwargs):
            raise AssertionError("priority checkpoint should stop before standings")

    monkeypatch.setattr(import_service, "client", Client())
    checkpoint_checks = iter([False, False, False, True])
    result = import_service.import_leagues_cautious(
        [61],
        seasons=[2026],
        pause=0,
        max_retries=1,
        resume_checkpoint="fixtures-page:61:2026:3",
        priority_preemption_check=lambda: next(checkpoint_checks),
    )
    assert requested == [("/fixtures", {"league": 61, "season": 2026, "page": 3})]
    assert result["preempted"] is True
    assert result["checkpoint"] == "fixtures-page:61:2026:4"
