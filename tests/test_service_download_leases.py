import unittest
from unittest.mock import MagicMock, patch

from services import lineup_service, player_service


class LineupLeaseIntegrationTests(unittest.TestCase):
    def test_lineup_download_skips_when_claim_is_held(self):
        with (
            patch.object(lineup_service, "_ensure_schema"),
            patch.object(lineup_service.sync_registry, "claim_download", return_value=False),
            patch.object(lineup_service.client, "get_fixture_lineups") as fetch,
        ):
            result = lineup_service.sync_lineups(123)

        self.assertTrue(result["skipped"])
        self.assertEqual(result["reason"], "claimed")
        fetch.assert_not_called()

    def test_lineup_download_releases_claim_after_success(self):
        session = MagicMock()
        session_context = MagicMock()
        session_context.__enter__.return_value = session
        session_context.__exit__.return_value = False
        claim = MagicMock(return_value=True)
        release = MagicMock()
        with (
            patch.object(lineup_service, "_ensure_schema"),
            patch.object(lineup_service.sync_registry, "claim_download", claim),
            patch.object(lineup_service.sync_registry, "release_download_claim", release),
            patch.object(lineup_service.client, "get_fixture_lineups", return_value={"response": []}),
            patch.object(lineup_service, "SessionLocal", return_value=session_context),
            patch.object(lineup_service, "_save_lineups", return_value=0),
        ):
            result = lineup_service.sync_lineups(123)

        self.assertFalse(result.get("skipped", False))
        self.assertFalse(result["available"])
        claim.assert_called_once_with("fixture-lineup:123", claim.call_args.args[1])
        release.assert_called_once_with("fixture-lineup:123", claim.call_args.args[1])

    def test_fixture_players_skip_when_claim_is_held(self):
        session = MagicMock()
        session.get.return_value = None
        session_context = MagicMock()
        session_context.__enter__.return_value = session
        session_context.__exit__.return_value = False
        with (
            patch.object(lineup_service, "_ensure_schema"),
            patch.object(lineup_service, "SessionLocal", return_value=session_context),
            patch.object(lineup_service.sync_registry, "claim_download", return_value=False),
            patch.object(lineup_service.client, "get_fixture_players") as fetch,
        ):
            result = lineup_service.sync_fixture_players(123)

        self.assertTrue(result["skipped"])
        fetch.assert_not_called()


class SeasonPlayerLeaseIntegrationTests(unittest.TestCase):
    def test_season_sync_skips_when_claim_is_held(self):
        claim = MagicMock(return_value=False)
        with (
            patch.object(player_service.sync_registry, "claim_download", claim),
            patch.object(player_service.client, "get_players") as fetch,
        ):
            result = player_service.sync_players(39, 2026)

        self.assertTrue(result["skipped"])
        self.assertEqual(result["reason"], "claimed")
        fetch.assert_not_called()

    def test_season_sync_releases_claim_after_success(self):
        claim = MagicMock(return_value=True)
        release = MagicMock()
        with (
            patch.object(player_service.sync_registry, "claim_download", claim),
            patch.object(player_service.sync_registry, "release_download_claim", release),
            patch.object(player_service, "_sync_players_unleased", return_value={"statistics": 4}) as sync,
        ):
            result = player_service.sync_players(39, 2026)

        self.assertEqual(result["statistics"], 4)
        claim.assert_called_once()
        release.assert_called_once_with(claim.call_args.args[0], claim.call_args.args[1])
        sync.assert_called_once_with(39, 2026, team_id=None, max_pages=100, progress_callback=sync.call_args.kwargs["progress_callback"])
        self.assertTrue(callable(sync.call_args.kwargs["progress_callback"]))


if __name__ == "__main__":
    unittest.main()
