import datetime
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, text

from services import sync_registry


class PersistentDownloadLeaseTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.datetime(2026, 11, 22, 7, 0, 0)
        self.now_patch = patch.object(sync_registry, "_now", return_value=self.now)
        self.now_patch.start()
        self.test_engine = create_engine("sqlite:///:memory:")
        self.engine_patch = patch.object(sync_registry, "engine", self.test_engine)
        self.engine_patch.start()
        sync_registry.ensure_lease_table()

    def tearDown(self):
        self.engine_patch.stop()
        self.test_engine.dispose()
        self.now_patch.stop()

    def test_claim_blocks_other_owner_and_allows_owner_renewal(self):
        self.assertTrue(sync_registry.claim_download("fixture:42", "worker-a"))
        self.assertFalse(sync_registry.claim_download("fixture:42", "worker-b"))
        self.assertTrue(sync_registry.claim_download("fixture:42", "worker-a"))

    def test_expired_claim_can_be_taken_over(self):
        self.assertTrue(
            sync_registry.claim_download("fixture:42", "worker-a", lease_seconds=10)
        )
        with self.test_engine.begin() as conn:
            conn.execute(
                text("UPDATE download_task_leases SET lease_until = :past"),
                {"past": (self.now - datetime.timedelta(seconds=1)).isoformat()},
            )
        self.assertTrue(sync_registry.claim_download("fixture:42", "worker-b"))

    def test_renew_and_release_require_owner(self):
        self.assertTrue(sync_registry.claim_download("fixture:42", "worker-a"))
        self.assertFalse(sync_registry.renew_download_claim("fixture:42", "worker-b"))
        self.assertTrue(sync_registry.renew_download_claim("fixture:42", "worker-a"))
        self.assertFalse(sync_registry.release_download_claim("fixture:42", "worker-b"))
        self.assertTrue(sync_registry.release_download_claim("fixture:42", "worker-a"))
        self.assertTrue(sync_registry.claim_download("fixture:42", "worker-b"))


if __name__ == "__main__":
    unittest.main()
