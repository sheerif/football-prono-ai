import datetime
import io
import threading
import unittest
from contextlib import redirect_stderr
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
            patch.object(run_scheduled_sync.remote_sync_lease, "claim", return_value=True),
            patch.object(run_scheduled_sync.sync_registry, "claim_download", return_value=True),
            patch.object(run_scheduled_sync.remote_sync_lease, "renew", return_value=True),
            patch.object(run_scheduled_sync.sync_registry, "renew_download_claim", return_value=True),
            patch.object(run_scheduled_sync.sync_registry, "release_download_claim") as release,
            patch.object(run_scheduled_sync.remote_sync_lease, "release") as remote_release,
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
            patch.object(run_scheduled_sync.remote_sync_lease, "claim", return_value=True),
            patch.object(run_scheduled_sync.sync_registry, "claim_download", return_value=False),
            patch.object(run_scheduled_sync.sync_registry, "get") as get_state,
            patch.object(run_scheduled_sync.prediction_archive_service, "archive_upcoming_predictions") as archive,
            patch.object(run_scheduled_sync.background_jobs, "api_quota_status") as quota,
            patch.object(run_scheduled_sync.sync_registry, "mark") as mark,
            patch.object(run_scheduled_sync.full_sync_service, "run_full_sync") as sync,
            patch.object(run_scheduled_sync.sync_registry, "release_download_claim") as release,
            patch.object(run_scheduled_sync.remote_sync_lease, "release") as remote_release,
        ):
            result = run_scheduled_sync.run()

        self.assertEqual(result["status"], "skipped")
        get_state.assert_not_called()
        archive.assert_not_called()
        quota.assert_not_called()
        mark.assert_not_called()
        sync.assert_not_called()
        release.assert_not_called()
        remote_release.assert_called_once()

    def test_scheduler_skips_without_api_calls_when_remote_lease_is_contended(self):
        with (
            patch.object(run_scheduled_sync.import_service, "init_db"),
            patch.object(run_scheduled_sync.sync_registry, "ensure_table"),
            patch.object(run_scheduled_sync.remote_sync_lease, "claim", return_value=False) as claim,
            patch.object(run_scheduled_sync.sync_registry, "claim_download") as local_claim,
            patch.object(run_scheduled_sync.remote_sync_lease, "renew") as renew,
            patch.object(run_scheduled_sync.remote_sync_lease, "release") as release,
            patch.object(run_scheduled_sync.prediction_archive_service, "archive_upcoming_predictions") as archive,
            patch.object(run_scheduled_sync.background_jobs, "api_quota_status") as quota,
            patch.object(run_scheduled_sync.sync_registry, "mark") as mark,
            patch.object(run_scheduled_sync.full_sync_service, "run_full_sync") as sync,
        ):
            result = run_scheduled_sync.run()

        self.assertEqual(result["status"], "skipped")
        claim.assert_called_once()
        local_claim.assert_not_called()
        renew.assert_not_called()
        release.assert_not_called()
        archive.assert_not_called()
        quota.assert_not_called()
        mark.assert_not_called()
        sync.assert_not_called()

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
            patch.object(run_scheduled_sync.remote_sync_lease, "claim", return_value=True),
            patch.object(run_scheduled_sync.sync_registry, "claim_download", return_value=True),
            patch.object(run_scheduled_sync.remote_sync_lease, "renew", return_value=True),
            patch.object(
                run_scheduled_sync.sync_registry,
                "renew_download_claim",
                side_effect=lambda *args, **kwargs: (renew.append(args) or len(renew) < 4),
            ),
            patch.object(run_scheduled_sync.sync_registry, "release_download_claim") as release,
            patch.object(run_scheduled_sync.remote_sync_lease, "release") as remote_release,
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

    def test_scheduler_stops_without_overwriting_state_after_remote_lease_loss(self):
        remote_renewals = []
        marks = []

        def fake_sync(progress_callback):
            progress_callback(1, 1, "phase")
            return {"quota_reached": False, "api_calls": 1, "errors": []}

        with (
            patch.object(run_scheduled_sync.import_service, "init_db"),
            patch.object(run_scheduled_sync.import_service, "record_update_log") as record_log,
            patch.object(run_scheduled_sync.sync_registry, "ensure_table"),
            patch.object(run_scheduled_sync.sync_registry, "get", return_value=None),
            patch.object(run_scheduled_sync.remote_sync_lease, "claim", return_value=True),
            patch.object(run_scheduled_sync.sync_registry, "claim_download", return_value=True),
            patch.object(run_scheduled_sync.remote_sync_lease, "renew", side_effect=lambda *args, **kwargs: (remote_renewals.append(args) or len(remote_renewals) < 4)),
            patch.object(run_scheduled_sync.sync_registry, "renew_download_claim", return_value=True) as local_renew,
            patch.object(run_scheduled_sync.sync_registry, "release_download_claim") as local_release,
            patch.object(run_scheduled_sync.remote_sync_lease, "release") as remote_release,
            patch.object(run_scheduled_sync.time, "monotonic", side_effect=[0, 0, 0, 0, 61]),
            patch.object(run_scheduled_sync.prediction_archive_service, "archive_upcoming_predictions", return_value={"archived": 0}),
            patch.object(run_scheduled_sync.background_jobs, "api_quota_status", return_value={"verified": True, "remaining": 50}),
            patch.object(run_scheduled_sync.sync_registry, "mark", side_effect=lambda *args, **kwargs: marks.append(args)),
            patch.object(run_scheduled_sync.full_sync_service, "run_full_sync", side_effect=fake_sync),
        ):
            with self.assertRaises(run_scheduled_sync.FullSyncLeaseLost):
                run_scheduled_sync.run()

        self.assertEqual([mark[2] for mark in marks], ["running"])
        self.assertEqual(local_renew.call_count, 3)
        record_log.assert_not_called()
        local_release.assert_called_once()
        remote_release.assert_called_once()

    def test_scheduler_renews_local_and_remote_leases_during_blocking_sync(self):
        def fake_sync(progress_callback):
            # Simulate a long operation that does not emit progress callbacks.
            threading.Event().wait(0.15)
            return {"quota_reached": False, "api_calls": 1, "errors": []}

        with (
            patch.object(run_scheduled_sync, "LEASE_RENEW_INTERVAL_SECONDS", 0.05),
            patch.object(run_scheduled_sync.import_service, "init_db"),
            patch.object(run_scheduled_sync.import_service, "record_update_log"),
            patch.object(run_scheduled_sync.sync_registry, "ensure_table"),
            patch.object(run_scheduled_sync.sync_registry, "get", return_value=None),
            patch.object(run_scheduled_sync.remote_sync_lease, "claim", return_value=True),
            patch.object(run_scheduled_sync.sync_registry, "claim_download", return_value=True),
            patch.object(run_scheduled_sync.remote_sync_lease, "renew", return_value=True) as remote_renew,
            patch.object(run_scheduled_sync.sync_registry, "renew_download_claim", return_value=True) as local_renew,
            patch.object(run_scheduled_sync.sync_registry, "release_download_claim"),
            patch.object(run_scheduled_sync.remote_sync_lease, "release"),
            patch.object(
                run_scheduled_sync.prediction_archive_service,
                "archive_upcoming_predictions",
                return_value={"status": "complete", "archived": 0, "api_calls": 0},
            ),
            patch.object(
                run_scheduled_sync.background_jobs,
                "api_quota_status",
                return_value={"verified": True, "remaining": 50, "current": 0, "limit": 100},
            ),
            patch.object(run_scheduled_sync.sync_registry, "mark"),
            patch.object(
                run_scheduled_sync.full_sync_service,
                "run_full_sync",
                side_effect=fake_sync,
            ) as sync,
        ):
            result = run_scheduled_sync.run()

        self.assertEqual(result["status"], "complete")
        sync.assert_called_once()
        self.assertGreaterEqual(remote_renew.call_count, 5)
        self.assertGreaterEqual(local_renew.call_count, 5)

    def test_scheduler_aborts_when_background_heartbeat_loses_lease(self):
        remote_renewals = []
        marks = []
        sync_started = threading.Event()

        def renew_remote(*args, **kwargs):
            remote_renewals.append(True)
            return not sync_started.is_set()

        def remember_mark(key, resource_type, status, **kwargs):
            marks.append(status)

        def fake_sync(progress_callback):
            sync_started.set()
            threading.Event().wait(0.15)
            return {"quota_reached": False, "api_calls": 1, "errors": []}

        with (
            patch.object(run_scheduled_sync, "LEASE_RENEW_INTERVAL_SECONDS", 0.05),
            patch.object(run_scheduled_sync.import_service, "init_db"),
            patch.object(run_scheduled_sync.import_service, "record_update_log"),
            patch.object(run_scheduled_sync.sync_registry, "ensure_table"),
            patch.object(run_scheduled_sync.sync_registry, "get", return_value=None),
            patch.object(run_scheduled_sync.remote_sync_lease, "claim", return_value=True),
            patch.object(run_scheduled_sync.sync_registry, "claim_download", return_value=True),
            patch.object(run_scheduled_sync.remote_sync_lease, "renew", side_effect=renew_remote),
            patch.object(run_scheduled_sync.sync_registry, "renew_download_claim", return_value=True),
            patch.object(run_scheduled_sync.sync_registry, "release_download_claim"),
            patch.object(run_scheduled_sync.remote_sync_lease, "release"),
            patch.object(
                run_scheduled_sync.prediction_archive_service,
                "archive_upcoming_predictions",
                return_value={"status": "complete", "archived": 0, "api_calls": 0},
            ),
            patch.object(
                run_scheduled_sync.background_jobs,
                "api_quota_status",
                return_value={"verified": True, "remaining": 50, "current": 0, "limit": 100},
            ),
            patch.object(run_scheduled_sync.sync_registry, "mark", side_effect=remember_mark),
            patch.object(
                run_scheduled_sync.full_sync_service,
                "run_full_sync",
                side_effect=fake_sync,
            ) as sync,
        ):
            with self.assertRaises(run_scheduled_sync.FullSyncLeaseLost):
                run_scheduled_sync.run()

        sync.assert_called_once()
        self.assertGreaterEqual(len(remote_renewals), 4)
        self.assertEqual(marks, ["running"])

    def test_scheduler_persists_successful_completion(self):
        marks = []

        def remember_mark(key, resource_type, status, **kwargs):
            marks.append((key, resource_type, status, kwargs))

        def fake_sync(progress_callback):
            progress_callback(1, 1, "phase")
            return {
                "quota_reached": False,
                "api_calls": 42,
                "xg_api_calls": 30,
                "errors": [],
            }

        with (
            patch.object(run_scheduled_sync.import_service, "init_db"),
            patch.object(run_scheduled_sync.import_service, "record_update_log"),
            patch.object(run_scheduled_sync.sync_registry, "ensure_table"),
            patch.object(run_scheduled_sync.sync_registry, "get", return_value=None),
            patch.object(run_scheduled_sync.remote_sync_lease, "claim", return_value=True),
            patch.object(run_scheduled_sync.sync_registry, "claim_download", return_value=True),
            patch.object(run_scheduled_sync.remote_sync_lease, "renew", return_value=True),
            patch.object(run_scheduled_sync.sync_registry, "renew_download_claim", return_value=True),
            patch.object(run_scheduled_sync.sync_registry, "release_download_claim"),
            patch.object(run_scheduled_sync.remote_sync_lease, "release"),
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
                side_effect=fake_sync,
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
        renewals_during_archive = []

        def archive_with_progress(**kwargs):
            callback = kwargs.get("progress_callback")
            if not callable(callback):
                raise AssertionError("scheduled archive must receive its lease heartbeat callback")
            callback(1, 1, "Archivage pré-match simulé")
            renewals_during_archive.append((remote_renew.call_count, local_renew.call_count))
            return archive

        with (
            patch.object(run_scheduled_sync.import_service, "init_db"),
            patch.object(run_scheduled_sync.import_service, "record_update_log"),
            patch.object(run_scheduled_sync.sync_registry, "ensure_table"),
            patch.object(run_scheduled_sync.sync_registry, "get", return_value=None),
            patch.object(run_scheduled_sync.remote_sync_lease, "claim", return_value=True),
            patch.object(run_scheduled_sync.sync_registry, "claim_download", return_value=True),
            patch.object(run_scheduled_sync.remote_sync_lease, "renew", return_value=True) as remote_renew,
            patch.object(run_scheduled_sync.sync_registry, "renew_download_claim", return_value=True) as local_renew,
            patch.object(run_scheduled_sync.time, "monotonic", side_effect=[0.0, 0.0, 61.0, 61.0, 61.0]),
            patch.object(run_scheduled_sync.sync_registry, "release_download_claim"),
            patch.object(run_scheduled_sync.remote_sync_lease, "release"),
            patch.object(
                run_scheduled_sync.prediction_archive_service,
                "archive_upcoming_predictions",
                side_effect=archive_with_progress,
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

        archive_predictions.assert_called_once()
        self.assertTrue(callable(archive_predictions.call_args.kwargs["progress_callback"]))
        self.assertEqual(renewals_during_archive, [(2, 2)])
        self.assertEqual(remote_renew.call_count, 4)
        self.assertEqual(local_renew.call_count, 4)
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
                return_value={"status": "uploaded", "remote_verified": True},
            ) as backup,
            patch.object(run_scheduled_sync.engine, "dispose") as dispose,
        ):
            result = run_scheduled_sync.main()

        self.assertEqual(result, 0)
        backup.assert_called_once_with(force=True)
        dispose.assert_called_once()

    def test_scheduled_entrypoint_persists_checkpoint_after_sync_exception(self):
        with (
            patch.object(
                run_scheduled_sync,
                "run",
                side_effect=RuntimeError("sync interrupted after checkpoint"),
            ),
            patch.object(
                run_scheduled_sync.git_backup,
                "backup_to_github",
                return_value={"status": "uploaded", "remote_verified": True},
            ) as backup,
            patch.object(run_scheduled_sync.engine, "dispose") as dispose,
        ):
            result = run_scheduled_sync.main()

        self.assertEqual(result, 1)
        backup.assert_called_once_with(force=True)
        dispose.assert_called_once()

    def test_scheduled_entrypoint_keeps_sync_error_when_error_checkpoint_backup_fails(self):
        stderr = io.StringIO()
        with (
            patch.object(
                run_scheduled_sync,
                "run",
                side_effect=RuntimeError("sync interrupted"),
            ),
            patch.object(
                run_scheduled_sync.git_backup,
                "backup_to_github",
                side_effect=RuntimeError("backup lease unavailable"),
            ) as backup,
            patch.object(run_scheduled_sync.engine, "dispose") as dispose,
            redirect_stderr(stderr),
        ):
            result = run_scheduled_sync.main()

        self.assertEqual(result, 1)
        backup.assert_called_once_with(force=True)
        dispose.assert_called_once()
        self.assertIn("sync interrupted", stderr.getvalue())
        self.assertIn("backup lease unavailable", stderr.getvalue())

    def test_scheduled_entrypoint_reports_unverified_error_checkpoint_without_success(self):
        stderr = io.StringIO()
        with (
            patch.object(
                run_scheduled_sync,
                "run",
                side_effect=RuntimeError("sync interrupted"),
            ),
            patch.object(
                run_scheduled_sync.git_backup,
                "backup_to_github",
                return_value={"status": "uploaded", "remote_verified": False},
            ) as backup,
            patch.object(run_scheduled_sync.engine, "dispose"),
            redirect_stderr(stderr),
        ):
            result = run_scheduled_sync.main()

        self.assertEqual(result, 1)
        backup.assert_called_once_with(force=True)
        self.assertIn("Checkpoint post-erreur non vérifié à distance", stderr.getvalue())
        self.assertIn("sync interrupted", stderr.getvalue())

    def test_scheduled_entrypoint_fails_when_post_sync_snapshot_is_not_verified(self):
        with (
            patch.object(run_scheduled_sync, "run", return_value={"status": "complete"}),
            patch.object(
                run_scheduled_sync.git_backup,
                "backup_to_github",
                return_value={"status": "skipped_daily_limit"},
            ) as backup,
            patch.object(run_scheduled_sync.engine, "dispose"),
        ):
            result = run_scheduled_sync.main()

        self.assertEqual(result, 1)
        backup.assert_called_once_with(force=True)

    def test_scheduled_entrypoint_fails_when_backup_is_not_remote_verified(self):
        with (
            patch.object(
                run_scheduled_sync,
                "run",
                return_value={"status": "complete"},
            ),
            patch.object(
                run_scheduled_sync.git_backup,
                "backup_to_github",
                return_value={
                    "status": "unchanged",
                    "integrity_verified": True,
                    "gzip_verified": True,
                    "remote_verified": False,
                },
            ),
            patch.object(run_scheduled_sync.engine, "dispose"),
        ):
            result = run_scheduled_sync.main()

        self.assertEqual(result, 1)

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

    def test_scheduled_entrypoint_fails_when_lease_contention_skips_sync(self):
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

        self.assertEqual(result, 1)
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

    def test_scheduler_persists_exact_quota_failure_diagnostics(self):
        marks = []
        quota_error = (
            "fixture-lineup:1570488: Appel API différé (lineups) : "
            "minute_quota; réserve protégée=1125."
        )
        sync_result = {
            "quota_reached": True,
            "api_calls": 400,
            "api_calls_by_family": {"compositions/statistiques joueurs": 400},
            "quota_status_by_family": {
                "compositions/statistiques joueurs": {
                    "daily_limit": "7500",
                    "daily_remaining": "6627",
                    "minute_limit": "300",
                    "minute_remaining": "0",
                    "checked_at": "2026-11-24T05:20:18",
                    "status_error": "",
                }
            },
            "quota_metrics": {
                "quota_policy_blocks": 1,
                "quota_atomic_blocks": 0,
                "quota_remote_block_denials": 0,
                "quota_remote_shared_tokens": 250,
            },
            "checkpoint": "fixture-lineup:1570488",
            "errors": [quota_error],
        }

        fixed_now = datetime.datetime(2026, 11, 24, 5, 20)
        with (
            patch.object(run_scheduled_sync, "_now", return_value=fixed_now),
            patch.object(run_scheduled_sync.import_service, "init_db"),
            patch.object(run_scheduled_sync.import_service, "record_update_log"),
            patch.object(run_scheduled_sync.sync_registry, "ensure_table"),
            patch.object(run_scheduled_sync.sync_registry, "get", return_value=None),
            patch.object(run_scheduled_sync.remote_sync_lease, "claim", return_value=True),
            patch.object(run_scheduled_sync.sync_registry, "claim_download", return_value=True),
            patch.object(run_scheduled_sync.remote_sync_lease, "renew", return_value=True),
            patch.object(run_scheduled_sync.sync_registry, "renew_download_claim", return_value=True),
            patch.object(run_scheduled_sync.sync_registry, "release_download_claim"),
            patch.object(run_scheduled_sync.remote_sync_lease, "release"),
            patch.object(
                run_scheduled_sync.prediction_archive_service,
                "archive_upcoming_predictions",
                return_value={"status": "complete", "archived": 0, "api_calls": 0},
            ),
            patch.object(
                run_scheduled_sync.background_jobs,
                "api_quota_status",
                return_value={"verified": True, "remaining": 6627, "minute_remaining": 300},
            ),
            patch.object(
                run_scheduled_sync.sync_registry,
                "mark",
                side_effect=lambda *args, **kwargs: marks.append((args, kwargs)),
            ),
            patch.object(
                run_scheduled_sync.full_sync_service,
                "run_full_sync",
                return_value=sync_result,
            ) as run_full_sync,
        ):
            result = run_scheduled_sync.run()

        self.assertEqual(result["status"], "waiting_quota")
        self.assertEqual(result["quota_kind"], "minute")
        expected_retry = fixed_now + datetime.timedelta(seconds=90)
        self.assertEqual(datetime.datetime.fromisoformat(result["next_retry_at"]), expected_retry)
        self.assertIn("minute", marks[-1][1]["message"].casefold())
        self.assertEqual(result["quota_error"], quota_error)
        self.assertEqual(result["api_calls_by_family"], sync_result["api_calls_by_family"])
        self.assertEqual(result["quota_status_by_family"], sync_result["quota_status_by_family"])
        self.assertEqual(result["quota_metrics"], sync_result["quota_metrics"])
        self.assertEqual(result["checkpoint"], "fixture-lineup:1570488")
        self.assertEqual(marks[-1][0][2], "waiting_quota")
        self.assertEqual(marks[-1][1]["metadata"]["quota_error"], quota_error)
        self.assertEqual(marks[-1][1]["metadata"]["quota_kind"], "minute")
        run_full_sync.assert_called_once()

    def test_scheduler_defers_without_data_calls_when_quota_is_exhausted(self):
        marks = []
        with (
            patch.object(run_scheduled_sync.import_service, "init_db"),
            patch.object(run_scheduled_sync.import_service, "record_update_log"),
            patch.object(run_scheduled_sync.sync_registry, "ensure_table"),
            patch.object(run_scheduled_sync.sync_registry, "get", return_value=None),
            patch.object(run_scheduled_sync.remote_sync_lease, "claim", return_value=True),
            patch.object(run_scheduled_sync.sync_registry, "claim_download", return_value=True),
            patch.object(run_scheduled_sync.remote_sync_lease, "renew", return_value=True),
            patch.object(run_scheduled_sync.sync_registry, "renew_download_claim", return_value=True),
            patch.object(run_scheduled_sync.sync_registry, "release_download_claim"),
            patch.object(run_scheduled_sync.remote_sync_lease, "release"),
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


    def test_scheduled_entrypoint_retries_minute_quota_after_verified_checkpoint_backup(self):
        fixed_now = datetime.datetime(2026, 11, 24, 5, 20)
        waiting = {
            "status": "waiting_quota",
            "quota_kind": "minute",
            "next_retry_at": (fixed_now + datetime.timedelta(seconds=90)).isoformat(),
            "checkpoint": "fixture-lineup:1570488",
            "api_calls": 400,
        }
        with (
            patch.object(run_scheduled_sync, "_now", return_value=fixed_now),
            patch.object(run_scheduled_sync, "run", side_effect=[waiting, {"status": "complete", "api_calls": 12}]) as run,
            patch.object(
                run_scheduled_sync.git_backup,
                "backup_to_github",
                side_effect=[
                    {"status": "uploaded", "remote_verified": True},
                    {"status": "unchanged", "remote_verified": True},
                ],
            ) as backup,
            patch.object(run_scheduled_sync.time, "sleep") as sleep,
            patch.object(run_scheduled_sync.engine, "dispose"),
        ):
            result = run_scheduled_sync.main()

        self.assertEqual(result, 0)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(backup.call_count, 2)
        self.assertTrue(all(item.kwargs == {"force": True} for item in backup.call_args_list))
        sleep.assert_called_once_with(90.0)

    def test_scheduled_entrypoint_does_not_retry_daily_quota(self):
        with (
            patch.object(
                run_scheduled_sync,
                "run",
                return_value={
                    "status": "waiting_quota",
                    "quota_kind": "daily",
                    "next_retry_at": "2026-11-25T00:00:00",
                },
            ) as run,
            patch.object(
                run_scheduled_sync.git_backup,
                "backup_to_github",
                return_value={"status": "uploaded", "remote_verified": True},
            ) as backup,
            patch.object(run_scheduled_sync.time, "sleep") as sleep,
            patch.object(run_scheduled_sync.engine, "dispose"),
        ):
            result = run_scheduled_sync.main()

        self.assertEqual(result, 1)
        run.assert_called_once()
        backup.assert_called_once_with(force=True)
        sleep.assert_not_called()

    def test_scheduled_entrypoint_never_retries_without_verified_checkpoint_backup(self):
        fixed_now = datetime.datetime(2026, 11, 24, 5, 20)
        with (
            patch.object(run_scheduled_sync, "_now", return_value=fixed_now),
            patch.object(
                run_scheduled_sync,
                "run",
                return_value={
                    "status": "waiting_quota",
                    "quota_kind": "minute",
                    "next_retry_at": (fixed_now + datetime.timedelta(seconds=90)).isoformat(),
                    "checkpoint": "fixture-lineup:1570488",
                },
            ) as run,
            patch.object(
                run_scheduled_sync.git_backup,
                "backup_to_github",
                return_value={"status": "uploaded", "remote_verified": False},
            ) as backup,
            patch.object(run_scheduled_sync.time, "sleep") as sleep,
            patch.object(run_scheduled_sync.engine, "dispose"),
        ):
            result = run_scheduled_sync.main()

        self.assertEqual(result, 1)
        run.assert_called_once()
        backup.assert_called_once_with(force=True)
        sleep.assert_not_called()

    def test_scheduled_entrypoint_bounds_transient_quota_retries(self):
        fixed_now = datetime.datetime(2026, 11, 24, 5, 20)
        waiting = {
            "status": "waiting_quota",
            "quota_kind": "minute",
            "next_retry_at": (fixed_now + datetime.timedelta(seconds=90)).isoformat(),
        }
        with (
            patch.object(run_scheduled_sync, "_now", return_value=fixed_now),
            patch.object(run_scheduled_sync, "MAX_TRANSIENT_QUOTA_RETRIES", 2),
            patch.object(run_scheduled_sync, "run", return_value=waiting) as run,
            patch.object(
                run_scheduled_sync.git_backup,
                "backup_to_github",
                return_value={"status": "unchanged", "remote_verified": True},
            ) as backup,
            patch.object(run_scheduled_sync.time, "sleep") as sleep,
            patch.object(run_scheduled_sync.engine, "dispose"),
        ):
            result = run_scheduled_sync.main()

        self.assertEqual(result, 1)
        self.assertEqual(run.call_count, 3)
        self.assertEqual(backup.call_count, 3)
        self.assertEqual(sleep.call_count, 2)


if __name__ == "__main__":
    unittest.main()
