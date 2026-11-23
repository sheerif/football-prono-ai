import unittest
from unittest.mock import MagicMock, patch

from services import lineup_service, player_service


class LineupLeaseIntegrationTests(unittest.TestCase):
    def test_lineup_download_skips_when_local_claim_is_held(self):
        with (
            patch.object(lineup_service, "_ensure_schema"),
            patch.object(lineup_service.sync_registry, "claim_download", return_value=False),
            patch.object(lineup_service.remote_sync_lease, "claim") as remote_claim,
            patch.object(lineup_service.client, "get_fixture_lineups") as fetch,
        ):
            result = lineup_service.sync_lineups(123)

        self.assertTrue(result["skipped"])
        self.assertEqual(result["reason"], "claimed")
        remote_claim.assert_not_called()
        fetch.assert_not_called()

    def test_lineup_download_skips_when_remote_claim_is_held(self):
        local_claim = MagicMock(return_value=True)
        local_release = MagicMock()
        with (
            patch.object(lineup_service, "_ensure_schema"),
            patch.object(lineup_service.sync_registry, "claim_download", local_claim),
            patch.object(lineup_service.sync_registry, "release_download_claim", local_release),
            patch.object(lineup_service.remote_sync_lease, "claim", return_value=False) as remote_claim,
            patch.object(lineup_service.remote_sync_lease, "release") as remote_release,
            patch.object(lineup_service.client, "get_fixture_lineups") as fetch,
        ):
            result = lineup_service.sync_lineups(123)

        self.assertTrue(result["skipped"])
        self.assertEqual(result["reason"], "remote_claimed")
        remote_claim.assert_called_once_with(
            "fixture-lineup:123",
            local_claim.call_args.args[1],
            lease_seconds=900,
        )
        remote_release.assert_not_called()
        local_release.assert_called_once_with("fixture-lineup:123", local_claim.call_args.args[1])
        fetch.assert_not_called()

    def test_lineup_download_fails_closed_when_remote_claim_unavailable(self):
        local_claim = MagicMock(return_value=True)
        local_release = MagicMock()
        with (
            patch.object(lineup_service, "_ensure_schema"),
            patch.object(lineup_service.sync_registry, "claim_download", local_claim),
            patch.object(lineup_service.sync_registry, "release_download_claim", local_release),
            patch.object(lineup_service.remote_sync_lease, "claim", side_effect=RuntimeError("remote unavailable")),
            patch.object(lineup_service.client, "get_fixture_lineups") as fetch,
        ):
            with self.assertRaisesRegex(RuntimeError, "aucun téléchargement lancé"):
                lineup_service.sync_lineups(123)

        local_release.assert_called_once_with("fixture-lineup:123", local_claim.call_args.args[1])
        fetch.assert_not_called()

    def test_lineup_download_releases_local_and_remote_claims_after_success(self):
        session = MagicMock()
        session_context = MagicMock()
        session_context.__enter__.return_value = session
        session_context.__exit__.return_value = False
        claim = MagicMock(return_value=True)
        release = MagicMock()
        remote_claim = MagicMock(return_value=True)
        remote_release = MagicMock()
        with (
            patch.object(lineup_service, "_ensure_schema"),
            patch.object(lineup_service.sync_registry, "claim_download", claim),
            patch.object(lineup_service.sync_registry, "release_download_claim", release),
            patch.object(lineup_service.remote_sync_lease, "claim", remote_claim),
            patch.object(lineup_service.remote_sync_lease, "release", remote_release),
            patch.object(lineup_service.client, "get_fixture_lineups", return_value={"response": []}),
            patch.object(lineup_service, "SessionLocal", return_value=session_context),
            patch.object(lineup_service, "_save_lineups", return_value=0),
        ):
            result = lineup_service.sync_lineups(123)

        self.assertFalse(result.get("skipped", False))
        self.assertFalse(result["available"])
        claim.assert_called_once_with("fixture-lineup:123", claim.call_args.args[1])
        remote_claim.assert_called_once_with(
            "fixture-lineup:123",
            claim.call_args.args[1],
            lease_seconds=900,
        )
        remote_release.assert_called_once_with("fixture-lineup:123", claim.call_args.args[1])
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
    def test_season_sync_skips_when_local_claim_is_held(self):
        local_claim = MagicMock(return_value=False)
        remote_claim = MagicMock(return_value=True)
        remote_release = MagicMock()
        with (
            patch.object(player_service.remote_sync_lease, "claim", remote_claim),
            patch.object(player_service.remote_sync_lease, "release", remote_release),
            patch.object(player_service.sync_registry, "claim_download", local_claim),
            patch.object(player_service.client, "get_players") as fetch,
        ):
            result = player_service.sync_players(39, 2026)

        self.assertTrue(result["skipped"])
        self.assertEqual(result["reason"], "local_claimed")
        local_claim.assert_called_once()
        remote_release.assert_called_once_with(
            local_claim.call_args.args[0], local_claim.call_args.args[1]
        )
        fetch.assert_not_called()

    def test_season_sync_skips_when_remote_claim_is_held(self):
        remote_claim = MagicMock(return_value=False)
        local_claim = MagicMock()
        with (
            patch.object(player_service.remote_sync_lease, "claim", remote_claim),
            patch.object(player_service.sync_registry, "claim_download", local_claim),
            patch.object(player_service.client, "get_players") as fetch,
            patch.object(player_service, "_sync_players_unleased") as sync,
        ):
            result = player_service.sync_players(39, 2026)

        self.assertTrue(result["skipped"])
        self.assertEqual(result["reason"], "remote_claimed")
        local_claim.assert_not_called()
        fetch.assert_not_called()
        sync.assert_not_called()

    def test_season_sync_fails_closed_when_remote_lease_unavailable(self):
        with (
            patch.object(player_service.remote_sync_lease, "claim", side_effect=RuntimeError("remote unavailable")),
            patch.object(player_service.sync_registry, "claim_download") as local_claim,
            patch.object(player_service.client, "get_players") as fetch,
            patch.object(player_service, "_sync_players_unleased") as sync,
        ):
            with self.assertRaisesRegex(RuntimeError, "aucun téléchargement lancé"):
                player_service.sync_players(39, 2026)

        local_claim.assert_not_called()
        fetch.assert_not_called()
        sync.assert_not_called()

    def test_season_sync_releases_both_claims_and_renews_at_page_checkpoint(self):
        local_claim = MagicMock(return_value=True)
        remote_claim = MagicMock(return_value=True)
        local_renew = MagicMock(return_value=True)
        remote_renew = MagicMock(return_value=True)
        local_release = MagicMock()
        remote_release = MagicMock()

        def sync_with_checkpoint(*args, **kwargs):
            kwargs["progress_callback"](1, 1, "Page 1/1 enregistrée · 12 joueur(s)")

        with (
            patch.object(player_service.remote_sync_lease, "claim", remote_claim),
            patch.object(player_service.remote_sync_lease, "renew", remote_renew),
            patch.object(player_service.remote_sync_lease, "release", remote_release),
            patch.object(player_service.sync_registry, "claim_download", local_claim),
            patch.object(player_service.sync_registry, "renew_download_claim", local_renew),
            patch.object(player_service.sync_registry, "release_download_claim", local_release),
            patch.object(player_service, "_sync_players_unleased", side_effect=sync_with_checkpoint),
        ):
            result = player_service.sync_players(39, 2026, team_id=10)

        self.assertFalse(result.get("skipped", False))
        remote_claim.assert_called_once_with(
            "season-players:39:2026",
            local_claim.call_args.args[1],
            lease_seconds=900,
        )
        local_renew.assert_called_once_with(
            "season-players:39:2026",
            local_claim.call_args.args[1],
            lease_seconds=900,
        )
        remote_renew.assert_called_once_with(
            "season-players:39:2026",
            local_claim.call_args.args[1],
            lease_seconds=900,
        )
        local_release.assert_called_once_with(
            "season-players:39:2026", local_claim.call_args.args[1]
        )
        remote_release.assert_called_once_with(
            "season-players:39:2026", local_claim.call_args.args[1]
        )

    def test_season_sync_stops_when_remote_lease_is_lost_at_checkpoint(self):
        local_claim = MagicMock(return_value=True)
        remote_claim = MagicMock(return_value=True)
        local_renew = MagicMock(return_value=True)
        remote_renew = MagicMock(return_value=False)
        local_release = MagicMock()
        remote_release = MagicMock()

        def sync_with_checkpoint(*args, **kwargs):
            kwargs["progress_callback"](1, 1, "Page 1/1 enregistrée · 12 joueur(s)")

        with (
            patch.object(player_service.remote_sync_lease, "claim", remote_claim),
            patch.object(player_service.remote_sync_lease, "renew", remote_renew),
            patch.object(player_service.remote_sync_lease, "release", remote_release),
            patch.object(player_service.sync_registry, "claim_download", local_claim),
            patch.object(player_service.sync_registry, "renew_download_claim", local_renew),
            patch.object(player_service.sync_registry, "release_download_claim", local_release),
            patch.object(player_service, "_sync_players_unleased", side_effect=sync_with_checkpoint),
        ):
            with self.assertRaisesRegex(RuntimeError, "Lease distante perdue"):
                player_service.sync_players(39, 2026)

        local_release.assert_called_once()
        remote_release.assert_called_once()




    def test_match_intelligence_reports_deferred_season_player_syncs(self):
        progress_events = []
        conn = MagicMock()
        conn.execute.return_value.scalar.return_value = 0
        context = MagicMock()
        context.__enter__.return_value = conn
        context.__exit__.return_value = False
        skipped = {"profiles": 0, "statistics": 0, "skipped": True, "reason": "remote_claimed"}
        with (
            patch.object(lineup_service, "_ensure_schema"),
            patch.object(lineup_service, "_fixture_league_id", return_value=39),
            patch.object(lineup_service, "sync_lineups", return_value={"skipped": True, "reason": "remote_claimed"}),
            patch.object(lineup_service.engine, "begin", return_value=context),
            patch.object(player_service, "sync_players", return_value=skipped),
            patch.object(lineup_service, "_recent_fixture_ids", return_value=[]),
            patch.object(lineup_service.time, "sleep"),
        ):
            result = lineup_service.sync_match_intelligence(
                fixture_id=500,
                home_team_id=10,
                away_team_id=20,
                season=2026,
                match_date="2026-11-23",
                progress_callback=lambda current, total, label: progress_events.append(label),
            )

        self.assertTrue(result["lineups_skipped"])
        self.assertEqual(result["season_players_skipped"], [
            {"team_id": 10, "reason": "remote_claimed"},
            {"team_id": 20, "reason": "remote_claimed"},
        ])
        self.assertEqual(result["season_players"], 0)
        self.assertFalse(result["errors"])
        self.assertTrue(any("différé" in label for label in progress_events))
        self.assertIn("effectifs saison ou compositions différés", progress_events[-1])


if __name__ == "__main__":
    unittest.main()
