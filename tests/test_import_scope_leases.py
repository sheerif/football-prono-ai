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
        lambda *args, **kwargs: calls.append("local_renew") or True,
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
        lambda *args, **kwargs: calls.append("renew") or False,
    )
    monkeypatch.setattr(
        sync_registry,
        "release_download_claim",
        lambda *args, **kwargs: calls.append("release") or True,
    )

    def fake_import(*args, progress_callback, priority_preemption_check, **kwargs):
        progress_callback(1, 3, "teams")
        assert priority_preemption_check() is True
        return {"preempted": True, "checkpoint": "teams:61:2026"}

    monkeypatch.setattr(
        import_service, "_import_leagues_cautious_unleased", fake_import
    )

    result = import_service.import_leagues_cautious([61], seasons=[2026])

    assert result["skipped"] is True
    assert result["lease_lost"] is True
    assert result["checkpoint"] == "core:61:2026"
    assert calls == ["local_renew", "local_renew", "local_release"]



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
