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
