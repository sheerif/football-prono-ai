import unittest
from unittest.mock import Mock, patch

import pandas as pd

from pages import matchs_a_venir


class FixtureDetailBatchLeaseTests(unittest.TestCase):
    def setUp(self):
        self.upcoming = pd.DataFrame(
            [{"fixture_id": 101, "league_id": 61, "season": 2026}]
        )

    def test_missing_details_skips_api_when_local_batch_lease_is_contended(self):
        with (
            patch.object(matchs_a_venir.schema_guard, "ensure_fixture_api_cache_tables"),
            patch.object(matchs_a_venir, "_load_cached_fixture_details", return_value={}),
            patch.object(matchs_a_venir.sync_registry, "should_download", return_value=True),
            patch.object(matchs_a_venir.sync_registry, "claim_download", return_value=False),
            patch.object(matchs_a_venir.sync_registry, "release_download_claim") as local_release,
            patch.object(matchs_a_venir.remote_sync_lease, "claim") as remote_claim,
            patch.object(matchs_a_venir.api_client, "get_fixtures") as request,
            patch.object(matchs_a_venir.sync_registry, "mark") as mark,
        ):
            result = matchs_a_venir._sync_missing_fixture_details(self.upcoming)

        self.assertEqual(result["skipped"], 1)
        request.assert_not_called()
        remote_claim.assert_not_called()
        local_release.assert_not_called()
        mark.assert_not_called()

    def test_missing_details_skips_api_on_remote_contention_and_releases_local(self):
        local_claim = Mock(return_value=True)
        local_release = Mock(return_value=True)
        remote_claim = Mock(return_value=False)
        with (
            patch.object(matchs_a_venir.schema_guard, "ensure_fixture_api_cache_tables"),
            patch.object(matchs_a_venir, "_load_cached_fixture_details", return_value={}),
            patch.object(matchs_a_venir.sync_registry, "should_download", return_value=True),
            patch.object(matchs_a_venir.sync_registry, "claim_download", local_claim),
            patch.object(matchs_a_venir.sync_registry, "release_download_claim", local_release),
            patch.object(matchs_a_venir.remote_sync_lease, "claim", remote_claim),
            patch.object(matchs_a_venir.remote_sync_lease, "release") as remote_release,
            patch.object(matchs_a_venir.api_client, "get_fixtures") as request,
            patch.object(matchs_a_venir.sync_registry, "mark") as mark,
        ):
            result = matchs_a_venir._sync_missing_fixture_details(self.upcoming)

        resource_key = "fixture-details-batch:61:2026"
        owner = local_claim.call_args.args[1]
        self.assertEqual(result["skipped"], 1)
        local_claim.assert_called_once_with(resource_key, owner)
        remote_claim.assert_called_once_with(
            resource_key,
            owner,
            lease_seconds=matchs_a_venir.remote_sync_lease.LEASE_SECONDS,
        )
        local_release.assert_called_once_with(resource_key, owner)
        remote_release.assert_not_called()
        request.assert_not_called()
        mark.assert_not_called()

    def test_missing_details_fails_closed_when_remote_lease_is_unavailable(self):
        local_claim = Mock(return_value=True)
        local_release = Mock(return_value=True)
        with (
            patch.object(matchs_a_venir.schema_guard, "ensure_fixture_api_cache_tables"),
            patch.object(matchs_a_venir, "_load_cached_fixture_details", return_value={}),
            patch.object(matchs_a_venir.sync_registry, "should_download", return_value=True),
            patch.object(matchs_a_venir.sync_registry, "claim_download", local_claim),
            patch.object(matchs_a_venir.sync_registry, "release_download_claim", local_release),
            patch.object(
                matchs_a_venir.remote_sync_lease,
                "claim",
                side_effect=RuntimeError("remote unavailable"),
            ),
            patch.object(matchs_a_venir.remote_sync_lease, "release") as remote_release,
            patch.object(matchs_a_venir.api_client, "get_fixtures") as request,
            patch.object(matchs_a_venir.sync_registry, "mark") as mark,
        ):
            result = matchs_a_venir._sync_missing_fixture_details(self.upcoming)

        self.assertEqual(result["skipped"], 1)
        local_release.assert_called_once()
        remote_release.assert_not_called()
        request.assert_not_called()
        mark.assert_not_called()

    def test_missing_details_releases_both_leases_after_success(self):
        local_claim = Mock(return_value=True)
        local_release = Mock(return_value=True)
        remote_claim = Mock(return_value=True)
        remote_release = Mock(return_value=True)
        complete_detail = {
            101: {
                "api_round": "Regular Season - 1",
                "api_home_logo": "home.png",
                "api_away_logo": "away.png",
            }
        }
        with (
            patch.object(matchs_a_venir.schema_guard, "ensure_fixture_api_cache_tables"),
            patch.object(
                matchs_a_venir,
                "_load_cached_fixture_details",
                side_effect=[{}, complete_detail],
            ),
            patch.object(matchs_a_venir.sync_registry, "should_download", return_value=True),
            patch.object(matchs_a_venir.sync_registry, "claim_download", local_claim),
            patch.object(matchs_a_venir.sync_registry, "release_download_claim", local_release),
            patch.object(matchs_a_venir.remote_sync_lease, "claim", remote_claim),
            patch.object(matchs_a_venir.remote_sync_lease, "release", remote_release),
            patch.object(
                matchs_a_venir.api_client,
                "get_fixtures",
                return_value={"response": [{"fixture": {"id": 101}}]},
            ) as request,
            patch.object(
                matchs_a_venir, "_save_fixture_details",
                return_value={"inserted": 1, "updated": 0, "unchanged": 0},
            ) as save,
            patch.object(matchs_a_venir.sync_registry, "mark") as mark,
        ):
            result = matchs_a_venir._sync_missing_fixture_details(self.upcoming)

        resource_key = "fixture-details-batch:61:2026"
        owner = local_claim.call_args.args[1]
        self.assertEqual(result["inserted"], 1)
        self.assertEqual(result["skipped"], 0)
        request.assert_called_once_with(61, 2026)
        save.assert_called_once()
        local_release.assert_called_once_with(resource_key, owner)
        remote_release.assert_called_once_with(resource_key, owner)
        self.assertEqual([call.args[2] for call in mark.call_args_list], ["running", "complete"])

    def test_manual_bulk_details_skips_api_when_batch_lease_is_contended(self):
        with (
            patch.object(matchs_a_venir.schema_guard, "ensure_fixture_api_cache_tables"),
            patch.object(matchs_a_venir.sync_registry, "claim_download", return_value=False),
            patch.object(matchs_a_venir.remote_sync_lease, "claim") as remote_claim,
            patch.object(matchs_a_venir.api_client, "get_fixtures") as request,
        ):
            result = matchs_a_venir._sync_fixture_details(self.upcoming)

        self.assertEqual(result["skipped"], 1)
        request.assert_not_called()
        remote_claim.assert_not_called()

    def test_manual_bulk_details_releases_leases_after_api_error(self):
        local_claim = Mock(return_value=True)
        local_release = Mock(return_value=True)
        remote_claim = Mock(return_value=True)
        remote_release = Mock(return_value=True)
        with (
            patch.object(matchs_a_venir.schema_guard, "ensure_fixture_api_cache_tables"),
            patch.object(matchs_a_venir.sync_registry, "claim_download", local_claim),
            patch.object(matchs_a_venir.sync_registry, "release_download_claim", local_release),
            patch.object(matchs_a_venir.remote_sync_lease, "claim", remote_claim),
            patch.object(matchs_a_venir.remote_sync_lease, "release", remote_release),
            patch.object(
                matchs_a_venir.api_client,
                "get_fixtures",
                side_effect=RuntimeError("quota"),
            ),
        ):
            result = matchs_a_venir._sync_fixture_details(self.upcoming)

        resource_key = "fixture-details-batch:61:2026"
        owner = local_claim.call_args.args[1]
        self.assertEqual(result["errors"], 1)
        local_release.assert_called_once_with(resource_key, owner)
        remote_release.assert_called_once_with(resource_key, owner)


if __name__ == "__main__":
    unittest.main()
