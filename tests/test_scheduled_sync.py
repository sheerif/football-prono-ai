import datetime
import unittest
from unittest.mock import patch

from scripts import run_scheduled_sync


class ScheduledSyncTests(unittest.TestCase):
    def test_recent_persistent_job_prevents_duplicate_sync(self):
        state = {
            "status": "running",
            "updated_at": datetime.datetime.now(datetime.UTC)
            .replace(tzinfo=None)
            .isoformat(),
        }
        with (
            patch.object(run_scheduled_sync.import_service, "init_db"),
            patch.object(run_scheduled_sync.sync_registry, "ensure_table"),
            patch.object(run_scheduled_sync.sync_registry, "get", return_value=state),
            patch.object(run_scheduled_sync.full_sync_service, "run_full_sync") as sync,
        ):
            result = run_scheduled_sync.run()

        self.assertEqual(result["status"], "skipped")
        sync.assert_not_called()

    def test_scheduler_persists_successful_completion(self):
        marks = []

        def remember_mark(key, resource_type, status, **kwargs):
            marks.append((key, resource_type, status, kwargs))

        with (
            patch.object(run_scheduled_sync.import_service, "init_db"),
            patch.object(run_scheduled_sync.import_service, "record_update_log"),
            patch.object(run_scheduled_sync.sync_registry, "ensure_table"),
            patch.object(run_scheduled_sync.sync_registry, "get", return_value=None),
            patch.object(
                run_scheduled_sync.background_jobs,
                "api_quota_status",
                return_value={"verified": True, "remaining": 58, "current": 42, "limit": 100},
            ),
            patch.object(run_scheduled_sync.sync_registry, "mark", side_effect=remember_mark),
            patch.object(
                run_scheduled_sync.full_sync_service,
                "run_full_sync",
                return_value={
                    "quota_reached": False,
                    "api_calls": 42,
                    "xg_api_calls": 30,
                    "errors": [],
                },
            ),
        ):
            result = run_scheduled_sync.run()

        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["api_calls"], 42)
        self.assertEqual([mark[2] for mark in marks], ["running", "complete"])

    def test_scheduler_defers_without_data_calls_when_quota_is_exhausted(self):
        marks = []
        with (
            patch.object(run_scheduled_sync.import_service, "init_db"),
            patch.object(run_scheduled_sync.import_service, "record_update_log"),
            patch.object(run_scheduled_sync.sync_registry, "ensure_table"),
            patch.object(run_scheduled_sync.sync_registry, "get", return_value=None),
            patch.object(
                run_scheduled_sync.background_jobs,
                "api_quota_status",
                return_value={
                    "verified": True,
                    "available": False,
                    "remaining": 0,
                    "current": 100,
                    "limit": 100,
                    "minute_remaining": 10,
                },
            ),
            patch.object(
                run_scheduled_sync.sync_registry,
                "mark",
                side_effect=lambda *args, **kwargs: marks.append((args, kwargs)),
            ),
            patch.object(
                run_scheduled_sync.full_sync_service, "run_full_sync"
            ) as run_full_sync,
        ):
            result = run_scheduled_sync.run()

        self.assertEqual(result["status"], "waiting_quota")
        self.assertEqual(marks[0][0][2], "waiting_quota")
        run_full_sync.assert_not_called()


if __name__ == "__main__":
    unittest.main()
