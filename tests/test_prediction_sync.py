import os
import unittest
from unittest.mock import Mock, patch

from services import background_jobs, full_sync_service


class PredictionSyncTests(unittest.TestCase):
    def test_invalid_pause_environment_uses_safe_default(self):
        with patch.dict(os.environ, {"PREDICTION_SYNC_PAUSE_SECONDS": "invalide"}):
            self.assertEqual(
                full_sync_service._nonnegative_float_env(
                    "PREDICTION_SYNC_PAUSE_SECONDS", 0.25
                ),
                0.25,
            )
        with patch.dict(os.environ, {"PREDICTION_SYNC_PAUSE_SECONDS": "nan"}):
            self.assertEqual(
                full_sync_service._nonnegative_float_env(
                    "PREDICTION_SYNC_PAUSE_SECONDS", 0.25
                ),
                0.25,
            )

    def test_resume_skips_recent_unavailable_resource(self):
        with (
            patch.object(
                full_sync_service.sync_registry,
                "get",
                return_value={"status": "unavailable"},
            ),
            patch.object(
                full_sync_service.sync_registry,
                "should_download",
                return_value=False,
            ),
            patch.object(full_sync_service.sync_registry, "mark") as mark,
        ):
            result = full_sync_service._fetch_one(
                "fixture-prediction:1",
                "fixture_prediction",
                Mock(),
                Mock(),
            )
        self.assertEqual(result, "skipped")
        mark.assert_not_called()

    def test_unavailable_response_is_persisted(self):
        with (
            patch.object(full_sync_service.sync_registry, "get", return_value=None),
            patch.object(full_sync_service.sync_registry, "mark") as mark,
        ):
            result = full_sync_service._fetch_one(
                "fixture-prediction:2",
                "fixture_prediction",
                lambda: {"response": []},
                Mock(),
            )
        self.assertEqual(result, "unavailable")
        self.assertEqual(mark.call_args_list[-1].args[2], "unavailable")

    def test_network_error_is_recorded_and_next_item_continues(self):
        rows = [{"fixture_id": 1}, {"fixture_id": 2}]
        with (
            patch.object(full_sync_service, "_prediction_present", return_value=False),
            patch.object(
                full_sync_service,
                "_fetch_one",
                side_effect=[ConnectionError("réseau"), "downloaded"],
            ),
            patch.object(full_sync_service.time, "sleep"),
        ):
            result = full_sync_service._sync_prediction_rows(
                rows, pause=0, retry_hours=12
            )
        self.assertEqual(result["downloaded"], 1)
        self.assertEqual(len(result["errors"]), 1)
        self.assertFalse(result["quota_reached"])

    def test_quota_error_stops_without_requesting_following_items(self):
        rows = [{"fixture_id": 1}, {"fixture_id": 2}]
        fetch = Mock(side_effect=RuntimeError("429 quota reached"))
        with (
            patch.object(full_sync_service, "_prediction_present", return_value=False),
            patch.object(full_sync_service, "_fetch_one", fetch),
        ):
            result = full_sync_service._sync_prediction_rows(
                rows, pause=0, retry_hours=12
            )
        self.assertTrue(result["quota_reached"])
        self.assertEqual(fetch.call_count, 1)

    def test_already_saved_item_does_not_call_api(self):
        with (
            patch.object(full_sync_service, "_prediction_present", return_value=True),
            patch.object(full_sync_service, "_fetch_one") as fetch,
            patch.object(full_sync_service.sync_registry, "mark"),
        ):
            result = full_sync_service._sync_prediction_rows(
                [{"fixture_id": 3}], pause=0, retry_hours=12
            )
        self.assertEqual(result["skipped"], 1)
        fetch.assert_not_called()

    def test_two_prediction_sync_launches_share_one_job(self):
        with patch.object(background_jobs, "_jobs", {}):
            first, first_created = background_jobs._create_unique_data_job(
                "prediction_sync", "Premier"
            )
            second, second_created = background_jobs._create_unique_data_job(
                "prediction_sync", "Deuxième"
            )
        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
