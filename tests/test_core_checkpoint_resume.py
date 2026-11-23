import pytest

from database import models
from services import import_service, remote_sync_lease, sync_registry


class _FakeQuery:
    def __init__(self, model):
        self.model = model

    def filter_by(self, **_kwargs):
        return self

    def count(self):
        if self.model is models.Team:
            return 4
        if self.model is models.Standing:
            return 1
        return 0


class _FakeSession:
    def query(self, model):
        return _FakeQuery(model)

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


class _PagedFixtureClient:
    def __init__(self):
        self.first_page_calls = 0
        self.page_calls = []
        self.fail_page_2 = True

    def get_fixtures(self, league_id, season):
        self.first_page_calls += 1
        return {
            "response": [{"fixture": {"id": 1001}, "teams": {}}],
            "paging": {"current": 1, "total": 3},
        }

    def _get(self, path, params):
        page = int(params["page"])
        self.page_calls.append(page)
        if self.fail_page_2 and page == 2:
            raise RuntimeError("quota limit 429")
        return {
            "response": [{"fixture": {"id": 1000 + page}, "teams": {}}],
            "paging": {"current": page, "total": 3},
        }

    def get_standings(self, league_id, season):
        return {"response": []}


def test_core_fixture_quota_checkpoint_persists_and_resume_starts_exact_page(monkeypatch):
    state = {"status": None, "metadata": {}}
    marks = []

    def mark(_key, _resource_type, status, *, message=None, metadata=None, **_kwargs):
        state["status"] = status
        state["message"] = message
        state["metadata"] = dict(metadata or {})
        marks.append((status, dict(metadata or {})))

    monkeypatch.setattr(import_service, "SessionLocal", _FakeSession)
    monkeypatch.setattr(import_service, "register_league_seasons", lambda *args, **kwargs: None)
    monkeypatch.setattr(import_service, "_get_or_create_team", lambda *args, **kwargs: None)
    monkeypatch.setattr(import_service, "_save_match", lambda *args, **kwargs: None)
    monkeypatch.setattr(import_service, "_save_fixture_api_detail", lambda *args, **kwargs: None)
    monkeypatch.setattr(import_service.time, "sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(sync_registry, "get", lambda _key: dict(state))
    monkeypatch.setattr(sync_registry, "mark", mark)
    monkeypatch.setattr(sync_registry, "claim_download", lambda *args, **kwargs: True)
    monkeypatch.setattr(sync_registry, "renew_download_claim", lambda *args, **kwargs: True)
    monkeypatch.setattr(sync_registry, "release_download_claim", lambda *args, **kwargs: True)
    monkeypatch.setattr(remote_sync_lease, "claim", lambda *args, **kwargs: True)
    monkeypatch.setattr(remote_sync_lease, "renew", lambda *args, **kwargs: True)
    monkeypatch.setattr(remote_sync_lease, "release", lambda *args, **kwargs: True)
    client = _PagedFixtureClient()
    monkeypatch.setattr(import_service, "client", client)

    with pytest.raises(RuntimeError, match="quota limit 429"):
        import_service.import_leagues_cautious(
            [61],
            seasons=[2026],
            pause=0,
            max_retries=1,
            force_refresh_seasons=[2026],
        )

    checkpoint = state["metadata"]["checkpoint"]
    assert checkpoint == "fixtures-page:61:2026:2"
    assert state["status"] == "error"
    assert any(
        metadata.get("checkpoint") == checkpoint
        for status, metadata in marks
        if status == "partial"
    )

    client.fail_page_2 = False
    result = import_service.import_leagues_cautious(
        [61],
        seasons=[2026],
        pause=0,
        max_retries=1,
        force_refresh_seasons=[2026],
        resume_checkpoint=checkpoint,
    )

    assert result is None
    assert client.first_page_calls == 1, "resume must not redownload page 1"
    assert client.page_calls == [2, 2, 3]
    assert state["status"] == "complete"
