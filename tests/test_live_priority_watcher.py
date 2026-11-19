from services import live_priority_watcher


def test_polling_interval_is_fast_when_live():
    assert live_priority_watcher._interval_seconds(has_live=True, quota={}) <= 120


def test_polling_interval_slows_when_daily_quota_low():
    assert live_priority_watcher._interval_seconds(
        has_live=True, quota={"daily_remaining": 500}
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
        last_rate_limit = {"daily_remaining": 4000}

        def _get(self, path, params):
            assert path == "/fixtures"
            assert params == {"live": "all"}
            return {"response": [{"fixture": {"id": 123}}]}

    monkeypatch.setattr(live_priority_watcher, "_persist_live_fixtures", lambda items: 1)
    result = live_priority_watcher.poll_once(FakeClient())
    assert result["live_count"] == 1
    assert result["changed"] == 1
