import datetime
import unittest
from unittest.mock import patch

from scripts import run_scheduled_sync


class ScheduledSyncTests(unittest.TestCase):
    def test_recent_running_marker_does_not_block_when_lease_is_free(self):
        state = {
            "status": "running",
            "updated_at": datetime.datetime.now(datetime.UTC)
            .replace(tzinfo=None)
            .isoformat(),
        }
        with (
            patch.object(run_scheduled_sync.import_service, "init_db"),
            patch.object(run_scheduled_sync.import_service, "record_update_log"),
            patch.object(run_scheduled_sync.sync_registry, "ensure_table"),
            patch.object(run_scheduled_sync.sync_registry, "get", return_value=state),
            patch.object(run_scheduled_sync.sync_registry, "claim_download", return_value=True),
            patch.object(run_scheduled_sync.sync_registry, "renew_download_claim", return_value=True),
            patch.object(run_scheduled_sync.sync_registry, "release_download_claim") as release,
            patch.object(run_scheduled_sync.prediction_archive_service, "archive_upcoming_predictions", return_value={"archived": 0}),
            patch.object(run_scheduled_sync.background_jobs, "api_quota_status", return_value={"verified": True, "remaining": 50}),
            patch.object(run_scheduled_sync.sync_registry, "mark"),
            patch.object(run_scheduled_sync.full_sync_service, "run_full_sync", return_value={"quota_reached": False, "api_calls": 1, "errors": []}) as sync,
        ):
            result = run_scheduled_sync.run()

        self.assertEqual(result["status"], "complete")
        sync.assert_called_once()
        release.assert_called_once()
        self.assertEqual(release.call_args.args[0], run_scheduled_sync.CONTROL_KEY)
        self.assertTrue(release.call_args.args[1].startswith("scheduled-sync:"))

    def test_scheduler_skips_without_api_calls_when_global_lease_is_contended(self):
        with (
            patch.object(run_scheduled_sync.import_service, "init_db"),
            patch.object(run_scheduled_sync.sync_registry, "ensure_table"),
            patch.object(run_scheduled_sync.sync_registry, "claim_download", return_value=False),
            patch.object(run_scheduled_sync.sync_registry, "get") as get_state,
            patch.object(run_scheduled_sync.prediction_archive_service, "archive_upcoming_predictions") as archive,
            patch.object(run_scheduled_sync.background_jobs, "api_quota_status") as quota,
            patch.object(run_scheduled_sync.sync_registry, "mark") as mark,
            patch.object(run_scheduled_sync.full_sync_service, "run_full_sync") as sync,
            patch.object(run_scheduled_sync.sync_registry, "release_download_claim") as release,
        ):
            result = run_scheduled_sync.run()

        self.assertEqual(result["status"], "skipped")
        get_state.assert_not_called()
        archive.assert_not_called()
        quota.assert_not_called()
        mark.assert_not_called()
        sync.assert_not_called()
        release.assert_not_called()

    def test_scheduler_stops_without_overwriting_state_after_lease_loss(self):
        renew = []
        marks = []

        def fake_sync(progress_callback):
            progress_callback(1, 1, "phase")
            return {"quota_reached": False, "api_calls": 1, "errors": []}

        with (
            patch.object(run_scheduled_sync.import_service, "init_db"),
            patch.object(run_scheduled_sync.import_service, "record_update_log") as record_log,
            patch.object(run_scheduled_sync.sync_registry, "ensure_table"),
            patch.object(run_scheduled_sync.sync_registry, "get", return_value=None),
            patch.object(run_scheduled_sync.sync_registry, "claim_download", return_value=True),
            patch.object(
                run_scheduled_sync.sync_registry,
                "renew_download_claim",
                side_effect=lambda *args, **kwargs: (renew.append(args) or len(renew) < 4),
            ),
            patch.object(run_scheduled_sync.sync_registry, "release_download_claim") as release,
            patch.object(run_scheduled_sync.time, "monotonic", side_effect=[0, 0, 0, 0, 61]),
            patch.object(run_scheduled_sync.prediction_archive_service, "archive_upcoming_predictions", return_value={"archived": 0}),
            patch.object(run_scheduled_sync.background_jobs, "api_quota_status", return_value={"verified": True, "remaining": 50}),
            patch.object(run_scheduled_sync.sync_registry, "mark", side_effect=lambda *args, **kwargs: marks.append(args)),
            patch.object(run_scheduled_sync.full_sync_service, "run_full_sync", side_effect=fake_sync),
        ):
            with self.assertRaises(run_scheduled_sync.FullSyncLeaseLost):
                run_scheduled_sync.run()

        self.assertEqual([mark[2] for mark in marks], ["running"])
        record_log.assert_not_called()
        release.assert_called_once()

    def test_scheduler_persists_successful_completion(self):
        marks = []

        def remember_mark(key, resource_type, status, **kwargs):
            marks.append((key, resource_type, status, kwargs))

        with (
            patch.object(run_scheduled_sync.import_service, "init_db"),
            patch.object(run_scheduled_sync.import_service, "record_update_log"),
            patch.object(run_scheduled_sync.sync_registry, "ensure_table"),
            patch.object(run_scheduled_sync.sync_registry, "get", return_value=None),
            patch.object(run_scheduled_sync.sync_registry, "claim_download", return_value=True),
            patch.object(run_scheduled_sync.sync_registry, "renew_download_claim", return_value=True),
            patch.object(run_scheduled_sync.sync_registry, "release_download_claim"),
            patch.object(
                run_scheduled_sync.prediction_archive_service,
                "archive_upcoming_predictions",
                return_value={"status": "complete", "archived": 2, "api_calls": 0},
            ),
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
            ) as sync,
        ):
            result = run_scheduled_sync.run()

        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["api_calls"], 42)
        self.assertTrue(callable(sync.call_args.kwargs["progress_callback"]))
        self.assertEqual([mark[2] for mark in marks], ["running", "complete"])

    def test_pre_match_archive_runs_before_quota_preflight(self):
        archive = {
            "status": "complete",
            "archived": 4,
            "skipped_insufficient_history": 1,
            "api_calls": 0,
        }
        with (
            patch.object(run_scheduled_sync.import_service, "init_db"),
            patch.object(run_scheduled_sync.import_service, "record_update_log"),
            patch.object(run_scheduled_sync.sync_registry, "ensure_table"),
            patch.object(run_scheduled_sync.sync_registry, "get", return_value=None),
            patch.object(run_scheduled_sync.sync_registry, "claim_download", return_value=True),
            patch.object(run_scheduled_sync.sync_registry, "renew_download_claim", return_value=True),
            patch.object(run_scheduled_sync.sync_registry, "release_download_claim"),
            patch.object(
                run_scheduled_sync.prediction_archive_service,
                "archive_upcoming_predictions",
                return_value=archive,
            ) as archive_predictions,
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
            patch.object(run_scheduled_sync.sync_registry, "mark"),
            patch.object(
                run_scheduled_sync.full_sync_service, "run_full_sync"
            ) as run_full_sync,
        ):
            result = run_scheduled_sync.run()

        archive_predictions.assert_called_once_with()
        self.assertEqual(result["status"], "waiting_quota")
        self.assertEqual(result["pre_match_archive"], archive)
        run_full_sync.assert_not_called()

    def test_scheduled_entrypoint_persists_database_after_sync(self):
        with (
            patch.object(
                run_scheduled_sync,
                "run",
                return_value={
                    "status": "complete",
                    "api_calls": 4,
                    "pre_match_archive": {"archived": 3},
                },
            ),
            patch.object(
                run_scheduled_sync.git_backup,
                "backup_to_github",
                return_value={"status": "uploaded"},
            ) as backup,
            patch.object(run_scheduled_sync.engine, "dispose") as dispose,
        ):
            result = run_scheduled_sync.main()

        self.assertEqual(result, 0)
        backup.assert_called_once_with(force=False)
        dispose.assert_called_once()

    def test_scheduled_entrypoint_accepts_daily_backup_limit_skip(self):
        with (
            patch.object(run_scheduled_sync, "run", return_value={"status": "complete"}),
            patch.object(
                run_scheduled_sync.git_backup,
                "backup_to_github",
                return_value={"status": "skipped_daily_limit"},
            ),
            patch.object(run_scheduled_sync.engine, "dispose"),
        ):
            result = run_scheduled_sync.main()

        self.assertEqual(result, 0)

    def test_scheduled_entrypoint_fails_when_sync_is_waiting_for_quota(self):
        with (
            patch.object(
                run_scheduled_sync,
                "run",
                return_value={"status": "waiting_quota", "api_calls": 0},
            ),
            patch.object(
                run_scheduled_sync.git_backup,
                "backup_to_github",
                return_value={"status": "uploaded"},
            ) as backup,
            patch.object(run_scheduled_sync.engine, "dispose"),
        ):
            result = run_scheduled_sync.main()

        self.assertEqual(result, 1)
        backup.assert_called_once_with(force=True)

    def test_scheduled_entrypoint_fails_when_sync_is_partial(self):
        with (
            patch.object(
                run_scheduled_sync,
                "run",
                return_value={"status": "partial", "api_calls": 12},
            ),
            patch.object(
                run_scheduled_sync.git_backup,
                "backup_to_github",
                return_value={"status": "uploaded"},
            ) as backup,
            patch.object(run_scheduled_sync.engine, "dispose"),
        ):
            result = run_scheduled_sync.main()

        self.assertEqual(result, 1)
        backup.assert_called_once_with(force=True)

    def test_scheduled_entrypoint_accepts_safe_lease_contention_skip(self):
        with (
            patch.object(
                run_scheduled_sync,
                "run",
                return_value={"status": "skipped", "reason": "active lease"},
            ),
            patch.object(run_scheduled_sync.git_backup, "backup_to_github") as backup,
            patch.object(run_scheduled_sync.engine, "dispose"),
        ):
            result = run_scheduled_sync.main()

        self.assertEqual(result, 0)
        backup.assert_not_called()

    def test_scheduled_entrypoint_fails_when_backup_is_not_configured(self):
        with (
            patch.object(run_scheduled_sync, "run", return_value={"status": "complete"}),
            patch.object(
                run_scheduled_sync.git_backup,
                "backup_to_github",
                return_value={"status": "not_configured"},
            ),
            patch.object(run_scheduled_sync.engine, "dispose"),
        ):
            result = run_scheduled_sync.main()

        self.assertEqual(result, 1)

    def test_scheduled_entrypoint_fails_when_database_backup_fails(self):
        with (
            patch.object(run_scheduled_sync, "run", return_value={"status": "complete"}),
            patch.object(
                run_scheduled_sync.git_backup,
                "backup_to_github",
                return_value={"status": "error"},
            ),
            patch.object(run_scheduled_sync.engine, "dispose"),
        ):
            result = run_scheduled_sync.main()

        self.assertEqual(result, 1)

    def test_scheduler_defers_without_data_calls_when_quota_is_exhausted(self):
        marks = []
        with (
            patch.object(run_scheduled_sync.import_service, "init_db"),
            patch.object(run_scheduled_sync.import_service, "record_update_log"),
            patch.object(run_scheduled_sync.sync_registry, "ensure_table"),
            patch.object(run_scheduled_sync.sync_registry, "get", return_value=None),
            patch.object(run_scheduled_sync.sync_registry, "claim_download", return_value=True),
            patch.object(run_scheduled_sync.sync_registry, "renew_download_claim", return_value=True),
            patch.object(run_scheduled_sync.sync_registry, "release_download_claim"),
            patch.object(
                run_scheduled_sync.prediction_archive_service,
                "archive_upcoming_predictions",
                return_value={"status": "complete", "archived": 2, "api_calls": 0},
            ),
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
