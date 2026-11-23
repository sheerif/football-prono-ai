import datetime
import pytest

from services import import_service, remote_sync_lease, sync_registry


def test_core_import_skips_without_api_when_scope_lease_is_contended(monkeypatch):
    progress = []
    monkeypatch.setattr(
        import_service, "register_league_seasons", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(sync_registry, "claim_download", lambda *args, **kwargs: False)
    monkeypatch.setattr(remote_sync_lease, "claim", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        import_service,
        "_import_leagues_cautious_unleased",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("contended scope must not call the importer/API")
        ),
    )

    result = import_service.import_leagues_cautious(
        [61], seasons=[2026], progress_callback=lambda *args: progress.append(args)
    )

    assert result == {
        "skipped": True,
        "skipped_scopes": ["core:61:2026"],
        "completed_scopes": [],
    }
    assert progress[-1][0:2] == (3, 3)
    assert "lease" in progress[-1][2].lower()


def test_core_import_renews_and_releases_lease(monkeypatch):
    calls = []
    ticks = iter([0, 61, 62])
    monkeypatch.setattr(import_service.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(sync_registry, "mark", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        import_service, "register_league_seasons", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        sync_registry,
        "claim_download",
        lambda key, owner, **kwargs: calls.append(("local_claim", key, owner)) or True,
    )
    monkeypatch.setattr(
        remote_sync_lease,
        "claim",
        lambda key, owner, **kwargs: calls.append(("remote_claim", key, owner)) or True,
    )
    monkeypatch.setattr(
        remote_sync_lease,
        "renew",
        lambda key, owner, **kwargs: calls.append(("remote_renew", key, owner)) or True,
    )
    monkeypatch.setattr(
        remote_sync_lease,
        "release",
        lambda key, owner: calls.append(("remote_release", key, owner)) or True,
    )
    monkeypatch.setattr(
        sync_registry,
        "renew_download_claim",
        lambda key, owner, **kwargs: calls.append(("local_renew", key, owner)) or True,
    )
    monkeypatch.setattr(
        sync_registry,
        "release_download_claim",
        lambda key, owner: calls.append(("local_release", key, owner)) or True,
    )

    def fake_import(*args, progress_callback, priority_preemption_check, **kwargs):
        progress_callback(1, 3, "teams")
        assert priority_preemption_check() is False
        return None

    monkeypatch.setattr(
        import_service, "_import_leagues_cautious_unleased", fake_import
    )

    result = import_service.import_leagues_cautious([61], seasons=[2026])

    assert result is None
    assert [item[0] for item in calls].count("local_claim") == 1
    assert [item[0] for item in calls].count("local_renew") >= 2
    assert [item[0] for item in calls].count("local_release") == 1
    assert [item[0] for item in calls].count("remote_claim") == 1
    assert [item[0] for item in calls].count("remote_renew") >= 1
    assert [item[0] for item in calls].count("remote_release") == 1
    assert calls[0][1] == "core:61:2026"
    assert calls[-1][1] == "core:61:2026"


def test_core_import_stops_when_lease_is_lost(monkeypatch):
    calls = []
    monkeypatch.setattr(sync_registry, "mark", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        import_service, "register_league_seasons", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        sync_registry, "claim_download", lambda *args, **kwargs: True
    )
    monkeypatch.setattr(remote_sync_lease, "claim", lambda *args, **kwargs: True)
    monkeypatch.setattr(remote_sync_lease, "renew", lambda *args, **kwargs: True)
    monkeypatch.setattr(remote_sync_lease, "release", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        sync_registry,
        "renew_download_claim",
        lambda *args, **kwargs: calls.append("local_renew") or False,
    )
    monkeypatch.setattr(
        sync_registry,
        "release_download_claim",
        lambda *args, **kwargs: calls.append("local_release") or True,
    )
    monkeypatch.setattr(
        import_service,
        "_import_leagues_cautious_unleased",
        lambda *args, priority_preemption_check, **kwargs: {
            "preempted": True,
            "checkpoint": "teams:61:2026",
        }
        if priority_preemption_check()
        else None,
    )

    result = import_service.import_leagues_cautious([61], seasons=[2026])

    assert result["skipped"] is True
    assert result["lease_lost"] is True
    assert result["checkpoint"] == "teams:61:2026"
    assert calls == ["local_renew", "local_release"]



def test_core_import_aborts_before_api_when_progress_renewal_fails(monkeypatch):
    calls = []
    ticks = iter([0, 61])
    monkeypatch.setattr(import_service.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(sync_registry, "mark", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        import_service, "register_league_seasons", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(sync_registry, "claim_download", lambda *args, **kwargs: True)
    monkeypatch.setattr(remote_sync_lease, "claim", lambda *args, **kwargs: True)
    monkeypatch.setattr(remote_sync_lease, "renew", lambda *args, **kwargs: False)
    monkeypatch.setattr(remote_sync_lease, "release", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        sync_registry,
        "renew_download_claim",
        lambda *args, **kwargs: calls.append("local_renew") or True,
    )
    monkeypatch.setattr(
        sync_registry,
        "release_download_claim",
        lambda *args, **kwargs: calls.append("local_release") or True,
    )

    def fake_import(*args, progress_callback, **kwargs):
        progress_callback(1, 3, "teams")
        raise AssertionError("API work must stop when remote renewal fails")

    monkeypatch.setattr(
        import_service, "_import_leagues_cautious_unleased", fake_import
    )

    result = import_service.import_leagues_cautious([61], seasons=[2026])

    assert result["skipped"] is True
    assert result["lease_lost"] is True
    assert result["checkpoint"] == "core:61:2026"
    assert calls == ["local_renew", "local_release"]



def test_core_import_skips_without_api_when_remote_scope_lease_is_contended(monkeypatch):
    calls = []
    monkeypatch.setattr(
        import_service, "register_league_seasons", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(sync_registry, "claim_download", lambda *args, **kwargs: True)
    monkeypatch.setattr(sync_registry, "release_download_claim", lambda *args, **kwargs: calls.append("local_release"))
    monkeypatch.setattr(remote_sync_lease, "claim", lambda *args, **kwargs: False)
    monkeypatch.setattr(remote_sync_lease, "renew", lambda *args, **kwargs: calls.append("remote_renew"))
    monkeypatch.setattr(remote_sync_lease, "release", lambda *args, **kwargs: calls.append("remote_release"))
    monkeypatch.setattr(
        import_service,
        "_import_leagues_cautious_unleased",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("remote contention must not call API/importer")
        ),
    )

    result = import_service.import_leagues_cautious([61], seasons=[2026])

    assert result == {
        "skipped": True,
        "skipped_scopes": ["core:61:2026"],
        "completed_scopes": [],
    }
    assert calls == ["local_release"]

def _configure_current_refresh(monkeypatch):
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
    monkeypatch.setattr(import_service, "_set_sync_value", lambda *args: None)
    monkeypatch.setattr(import_service, "register_league_seasons", lambda *args, **kwargs: None)
    monkeypatch.setattr(import_service, "SessionLocal", lambda: session)
    monkeypatch.setattr(import_service, "_active_season_for_league", lambda *args: 2026)
    monkeypatch.setattr(sync_registry, "mark", lambda *args, **kwargs: None)
    monkeypatch.setattr(sync_registry, "get", lambda key: None)
    return session


def test_current_refresh_skips_api_when_local_scope_lease_is_contended(monkeypatch):
    session = _configure_current_refresh(monkeypatch)
    monkeypatch.setattr(sync_registry, "claim_download", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        remote_sync_lease, "claim", lambda *args, **kwargs: pytest.fail("remote/API work must not start")
    )
    monkeypatch.setattr(
        import_service,
        "_refresh_league_season",
        lambda *args, **kwargs: pytest.fail("API refresh must not run"),
    )

    result = import_service.refresh_current_competitions_on_connection()

    assert result["partial"] is True
    assert result["skipped_scopes"] == ["core:61:2026"]
    assert result["refreshed"] == []
    assert session.closed


def test_current_refresh_skips_api_and_releases_local_claim_on_remote_contention(monkeypatch):
    session = _configure_current_refresh(monkeypatch)
    releases = []
    monkeypatch.setattr(sync_registry, "claim_download", lambda *args, **kwargs: True)
    monkeypatch.setattr(sync_registry, "release_download_claim", lambda *args: releases.append("local"))
    monkeypatch.setattr(remote_sync_lease, "claim", lambda *args, **kwargs: False)
    monkeypatch.setattr(remote_sync_lease, "release", lambda *args: releases.append("remote"))
    monkeypatch.setattr(
        import_service,
        "_refresh_league_season",
        lambda *args, **kwargs: pytest.fail("API refresh must not run on remote contention"),
    )

    result = import_service.refresh_current_competitions_on_connection()

    assert result["partial"] is True
    assert result["skipped_scopes"] == ["core:61:2026"]
    assert result["refreshed"] == []
    assert releases == ["local"]
    assert session.closed


def test_current_refresh_fails_closed_when_remote_lease_unavailable(monkeypatch):
    session = _configure_current_refresh(monkeypatch)
    releases = []
    monkeypatch.setattr(sync_registry, "claim_download", lambda *args, **kwargs: True)
    monkeypatch.setattr(sync_registry, "release_download_claim", lambda *args: releases.append("local"))
    monkeypatch.setattr(
        remote_sync_lease, "claim", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("remote unavailable"))
    )
    monkeypatch.setattr(remote_sync_lease, "release", lambda *args: releases.append("remote"))
    monkeypatch.setattr(
        import_service,
        "_refresh_league_season",
        lambda *args, **kwargs: pytest.fail("API refresh must not run when lease verification fails"),
    )

    result = import_service.refresh_current_competitions_on_connection()

    assert result["partial"] is True
    assert result["skipped_scopes"] == ["core:61:2026"]
    assert releases == ["local"]
    assert session.closed


def test_current_refresh_renews_and_releases_both_scope_leases(monkeypatch):
    session = _configure_current_refresh(monkeypatch)
    calls = []
    ticks = iter([0, 0, 61, 62])
    monkeypatch.setattr(import_service.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(
        sync_registry,
        "claim_download",
        lambda key, owner, **kwargs: calls.append(("local_claim", key, owner)) or True,
    )
    monkeypatch.setattr(
        remote_sync_lease,
        "claim",
        lambda key, owner, **kwargs: calls.append(("remote_claim", key, owner)) or True,
    )
    monkeypatch.setattr(
        sync_registry,
        "renew_download_claim",
        lambda key, owner, **kwargs: calls.append(("local_renew", key, owner)) or True,
    )
    monkeypatch.setattr(
        remote_sync_lease,
        "renew",
        lambda key, owner, **kwargs: calls.append(("remote_renew", key, owner)) or True,
    )
    monkeypatch.setattr(
        sync_registry,
        "release_download_claim",
        lambda key, owner: calls.append(("local_release", key, owner)) or True,
    )
    monkeypatch.setattr(
        remote_sync_lease,
        "release",
        lambda key, owner: calls.append(("remote_release", key, owner)) or True,
    )

    def fake_refresh(*args, progress_callback, priority_preemption_check, **kwargs):
        progress_callback(1, 3, "teams")
        assert priority_preemption_check() is False
        return None

    monkeypatch.setattr(import_service, "_refresh_league_season", fake_refresh)

    result = import_service.refresh_current_competitions_on_connection()

    assert result["refreshed"] == [{"league_id": 61, "season": 2026}]
    assert not result.get("partial", False)
    assert [call[0] for call in calls].count("local_claim") == 1
    assert [call[0] for call in calls].count("remote_claim") == 1
    assert [call[0] for call in calls].count("local_renew") == 1
    assert [call[0] for call in calls].count("remote_renew") == 1
    assert [call[0] for call in calls].count("local_release") == 1
    assert [call[0] for call in calls].count("remote_release") == 1
    assert session.closed


def test_current_refresh_stops_when_remote_lease_renewal_is_lost(monkeypatch):
    session = _configure_current_refresh(monkeypatch)
    calls = []
    ticks = iter([0, 61])
    monkeypatch.setattr(import_service.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(sync_registry, "claim_download", lambda *args, **kwargs: True)
    monkeypatch.setattr(remote_sync_lease, "claim", lambda *args, **kwargs: True)
    monkeypatch.setattr(sync_registry, "renew_download_claim", lambda *args, **kwargs: True)
    monkeypatch.setattr(remote_sync_lease, "renew", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        sync_registry, "release_download_claim", lambda *args: calls.append("local_release")
    )
    monkeypatch.setattr(
        remote_sync_lease, "release", lambda *args: calls.append("remote_release")
    )

    def fake_refresh(*args, priority_preemption_check, **kwargs):
        return {"preempted": True, "checkpoint": "teams:61:2026"} if priority_preemption_check() else None

    monkeypatch.setattr(import_service, "_refresh_league_season", fake_refresh)

    result = import_service.refresh_current_competitions_on_connection()

    assert result["lease_lost"] is True
    assert result["checkpoint"] == "teams:61:2026"
    assert result["skipped_scopes"] == ["core:61:2026"]
    assert result["refreshed"] == []
    assert calls == ["remote_release", "local_release"]
    assert session.closed

def test_current_refresh_reuses_recent_completed_scope_without_claim_or_api(monkeypatch):
    session = _configure_current_refresh(monkeypatch)
    updated_at = (
        datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
        - datetime.timedelta(minutes=5)
    ).isoformat()
    monkeypatch.setattr(
        sync_registry,
        "get",
        lambda key: {
            "status": "complete",
            "updated_at": updated_at,
        },
    )
    monkeypatch.setattr(
        sync_registry,
        "claim_download",
        lambda *args, **kwargs: pytest.fail("recent complete scope should not claim"),
    )
    monkeypatch.setattr(
        remote_sync_lease,
        "claim",
        lambda *args, **kwargs: pytest.fail("cached scope must not claim remote lease"),
    )
    monkeypatch.setattr(
        import_service,
        "_refresh_league_season",
        lambda *args, **kwargs: pytest.fail("cached scope must not call API"),
    )

    result = import_service.refresh_current_competitions_on_connection()

    assert result["refreshed"] == []
    assert result["cached_scopes"] == [{"league_id": 61, "season": 2026}]
    assert not result.get("partial", False)
    assert session.closed



def test_current_refresh_marks_failed_api_phases_incomplete(monkeypatch):
    class Query:
        def filter_by(self, **kwargs):
            return self
        def count(self):
            return 0

    class FakeSession:
        def get(self, model, league_id):
            return type("League", (), {"name": "Test", "logo": "logo"})()
        def query(self, model):
            return Query()
        def commit(self):
            pass
        def rollback(self):
            pass
        def close(self):
            pass

    session = FakeSession()
    monkeypatch.setattr(import_service, "time.sleep", lambda *_: None)
    monkeypatch.setattr(import_service.client, "get_teams", lambda *_: (_ for _ in ()).throw(RuntimeError("transient teams error")))
    monkeypatch.setattr(import_service.client, "get_fixtures", lambda *_: (_ for _ in ()).throw(RuntimeError("transient fixtures error")))
    monkeypatch.setattr(import_service.client, "get_standings", lambda *_: (_ for _ in ()).throw(RuntimeError("transient standings error")))

    result = import_service._refresh_league_season(
        session, league_id=61, season=2026, pause=0, max_retries=1
    )

    assert result["partial"] is True
    assert result["failed_phases"] == ["teams", "fixtures", "standings"]
    assert result["completed_phases"] == []


def test_current_refresh_defers_incomplete_phase_without_caching(monkeypatch):
    session = _configure_current_refresh(monkeypatch)
    states = []
    writes = []
    monkeypatch.setattr(sync_registry, "mark", lambda key, resource_type, status, **kwargs: states.append((key, status, kwargs.get("message"))))
    monkeypatch.setattr(sync_registry, "claim_download", lambda *args, **kwargs: True)
    monkeypatch.setattr(sync_registry, "release_download_claim", lambda *args: True)
    monkeypatch.setattr(remote_sync_lease, "claim", lambda *args, **kwargs: True)
    monkeypatch.setattr(remote_sync_lease, "release", lambda *args: True)
    monkeypatch.setattr(import_service, "_refresh_league_season", lambda *args, **kwargs: {
        "partial": True, "failed_phases": ["fixtures"], "completed_phases": ["teams", "standings"]
    })
    monkeypatch.setattr(import_service, "_set_sync_value", lambda *args: writes.append(args))

    result = import_service.refresh_current_competitions_on_connection()

    assert result["partial"] is True
    assert result["failed_phases"] == ["fixtures"]
    assert result["skipped_scopes"] == ["core:61:2026"]
    assert result["refreshed"] == []
    assert [status for _, status, _ in states] == ["running", "deferred"]
    assert writes == []
    assert session.closed
