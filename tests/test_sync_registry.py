import datetime
import unittest
from unittest.mock import patch

from services import sync_registry


class SyncRegistryTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.datetime(2026, 11, 17, 12, 0, 0)
        self.now_patch = patch.object(sync_registry, "_now", return_value=self.now)
        self.now_patch.start()

    def tearDown(self):
        self.now_patch.stop()

    def test_complete_resource_is_not_downloaded_again(self):
        with patch.object(
            sync_registry, "get", return_value={"status": "complete"}
        ):
            self.assertFalse(sync_registry.should_download("fixture:1"))

    def test_fresh_running_resource_is_not_downloaded_again(self):
        row = {
            "status": "running",
            "updated_at": (self.now - datetime.timedelta(minutes=5)).isoformat(),
        }
        with patch.object(sync_registry, "get", return_value=row):
            self.assertFalse(sync_registry.should_download("fixture:1"))

    def test_stale_running_resource_can_be_recovered(self):
        row = {
            "status": "running",
            "updated_at": (self.now - datetime.timedelta(minutes=31)).isoformat(),
        }
        with patch.object(sync_registry, "get", return_value=row):
            self.assertTrue(sync_registry.should_download("fixture:1"))

    def test_running_lease_duration_can_be_configured(self):
        row = {
            "status": "running",
            "updated_at": (self.now - datetime.timedelta(minutes=10)).isoformat(),
        }
        with patch.object(sync_registry, "get", return_value=row):
            self.assertFalse(
                sync_registry.should_download(
                    "fixture:1", running_retry_minutes=15
                )
            )
            self.assertTrue(
                sync_registry.should_download(
                    "fixture:1", running_retry_minutes=5
                )
            )

    def test_invalid_running_timestamp_allows_recovery(self):
        with patch.object(
            sync_registry,
            "get",
            return_value={"status": "running", "updated_at": "invalid"},
        ):
            self.assertTrue(sync_registry.should_download("fixture:1"))

    def test_unavailable_resource_keeps_existing_retry_delay(self):
        row = {
            "status": "unavailable",
            "updated_at": (self.now - datetime.timedelta(hours=11)).isoformat(),
        }
        with patch.object(sync_registry, "get", return_value=row):
            self.assertFalse(sync_registry.should_download("fixture:1"))
        row["updated_at"] = (
            self.now - datetime.timedelta(hours=13)
        ).isoformat()
        with patch.object(sync_registry, "get", return_value=row):
            self.assertTrue(sync_registry.should_download("fixture:1"))


if __name__ == "__main__":
    unittest.main()
