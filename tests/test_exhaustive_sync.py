import datetime
from contextlib import ExitStack
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from sqlalchemy import create_engine, text

from pages import data_management
from services import background_jobs, full_sync_service, sync_registry


class _ImmediateThread:
    def __init__(self, target, **_kwargs):
        self.target = target

    def start(self):
        self.target()


class ExhaustiveSyncTests(unittest.TestCase):
    def test_in_progress_fixture_query_only_returns_recent_live_statuses(self):
        test_engine = create_engine("sqlite://")
        with test_engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE matches (fixture_id INTEGER, league_id INTEGER, "
                    "season INTEGER, date TEXT, home_team_id INTEGER, "
                    "away_team_id INTEGER, home_goals INTEGER, away_goals INTEGER, "
                    "status TEXT)"
                )
            )
            connection.execute(
                text(
                    "CREATE TABLE fixture_api_details "
                    "(fixture_id INTEGER, status_short TEXT)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO matches VALUES "
                    "(1, 61, 2026, datetime('now', '-1 hour'), 10, 20, NULL, NULL, 'First Half'), "
                    "(2, 61, 2026, datetime('now', '-3 days'), 10, 20, NULL, NULL, 'Match Suspended'), "
                    "(3, 61, 2026, datetime('now', '-1 hour'), 10, 20, 1, 0, 'FT')"
                )
            )
            connection.execute(
                text("INSERT INTO fixture_api_details VALUES (3, 'FT')")
            )

        with patch.object(full_sync_service, "engine", test_engine):
            matches = full_sync_service._in_progress_matches()

        self.assertEqual([match["fixture_id"] for match in matches], [1])
        test_engine.dispose()

    def test_missing_core_scopes_are_computed_in_bulk(self):
        test_engine = create_engine("sqlite://")
        with test_engine.begin() as conn:
            conn.execute(text("CREATE TABLE matches (league_id INTEGER, season INTEGER)"))
            conn.execute(text("CREATE TABLE teams (league_id INTEGER)"))
            conn.execute(text("CREATE TABLE standings (league_id INTEGER, season INTEGER)"))
            conn.execute(text("INSERT INTO matches VALUES (61, 2026), (39, 2026)"))
            conn.execute(text("INSERT INTO teams VALUES (61), (39)"))
            conn.execute(text("INSERT INTO standings VALUES (61, 2026)"))

        with patch.object(full_sync_service, "engine", test_engine):
            missing = full_sync_service._missing_core_scopes(
                [61, 39], [2026]
            )

        self.assertEqual(missing, [(39, 2026)])

    def test_recent_fixture_ids_are_loaded_in_one_ranked_query(self):
        test_engine = create_engine("sqlite://")
        with test_engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE matches (fixture_id INTEGER, date TEXT, "
                    "home_team_id INTEGER, away_team_id INTEGER, "
                    "home_goals INTEGER, away_goals INTEGER)"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO matches VALUES "
                    "(1, '2026-09-01', 10, 30, 2, 0), "
                    "(2, '2026-09-02', 10, 40, 1, 1), "
                    "(3, '2026-09-03', 20, 30, 0, 1), "
                    "(4, '2026-09-04', 20, 40, 3, 2), "
                    "(5, '2026-09-11', 10, 20, NULL, NULL)"
                )
            )
        upcoming = [
            {
                "fixture_id": 5,
                "date": "2026-09-10",
                "home_team_id": 10,
                "away_team_id": 20,
            }
        ]

        with patch.object(full_sync_service, "engine", test_engine):
            fixture_ids = full_sync_service._recent_fixture_ids(
                upcoming, per_team=1
            )

        self.assertEqual(fixture_ids, [2, 4])

    def test_daily_reserve_is_disabled_by_default(self):
        api_client = Mock()
        api_client.request_count = 11
        api_client.last_rate_limit = {"daily_remaining": "0"}
        with patch.dict("os.environ", {}, clear=False):
            with patch("services.full_sync_service.os.getenv", return_value=None):
                self.assertFalse(
                    full_sync_service._daily_reserve_reached(api_client, 10)
                )

    def test_xg_reserves_requests_for_current_data_by_default(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(full_sync_service._xg_daily_reserve(), 1500)

    def test_daily_reserve_uses_a_current_pass_rate_limit_header(self):
        api_client = Mock()
        api_client.request_count = 11
        api_client.last_rate_limit = {"daily_remaining": "500"}
        with patch.dict(
            "os.environ", {"FULL_SYNC_DAILY_RESERVE": "500"}, clear=False
        ):
            self.assertTrue(
                full_sync_service._daily_reserve_reached(api_client, 10)
            )
            self.assertFalse(
                full_sync_service._daily_reserve_reached(api_client, 11)
            )

    def test_xg_backfill_skips_when_persistent_lease_is_held(self):
        api_client = Mock()
        api_client.request_count = 0
        rows = [
            {"fixture_id": 101, "date": "2026-11-21", "league_id": 61, "season": 2026}
        ]
        with (
            patch.object(full_sync_service, "client", api_client),
            patch.object(full_sync_service, "_priority_preemption_requested", return_value=False),
            patch.object(full_sync_service, "_daily_reserve_reached", return_value=False),
            patch.object(full_sync_service, "_xg_should_download", return_value=True),
            patch.object(full_sync_service.xg_service, "fixture_statistics_present", return_value=False),
            patch.object(full_sync_service.sync_registry, "get", return_value=None),
            patch.object(full_sync_service.sync_registry, "claim_download", return_value=False) as claim,
            patch.object(full_sync_service.sync_registry, "release_download_claim") as release,
            patch.object(full_sync_service.sync_registry, "mark") as mark,
            patch.object(full_sync_service.time, "sleep"),
        ):
            result = full_sync_service._sync_xg_rows(
                rows, pause=0, retry_hours=24, sync_run_id="lease-contention"
            )

        claim.assert_called_once()
        release.assert_not_called()
        mark.assert_not_called()
        api_client.get_fixture_statistics.assert_not_called()
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["lease_contention"], 1)
        self.assertEqual(result["api_calls"], 0)

    def test_xg_backfill_releases_lease_after_successful_fetch(self):
        api_client = Mock()
        api_client.request_count = 0
        api_client.get_fixture_statistics.return_value = {"response": []}
        rows = [
            {"fixture_id": 101, "date": "2026-11-21", "league_id": 61, "season": 2026}
        ]
        with (
            patch.object(full_sync_service, "client", api_client),
            patch.object(full_sync_service, "_priority_preemption_requested", return_value=False),
            patch.object(full_sync_service, "_daily_reserve_reached", return_value=False),
            patch.object(full_sync_service, "_xg_should_download", return_value=True),
            patch.object(full_sync_service.xg_service, "fixture_statistics_present", return_value=False),
            patch.object(full_sync_service.xg_service, "record_ingestion"),
            patch.object(full_sync_service.sync_registry, "get", return_value=None),
            patch.object(full_sync_service.sync_registry, "claim_download", return_value=True) as claim,
            patch.object(full_sync_service.sync_registry, "release_download_claim") as release,
            patch.object(full_sync_service.sync_registry, "mark"),
            patch.object(full_sync_service.time, "sleep"),
        ):
            result = full_sync_service._sync_xg_rows(
                rows, pause=0, retry_hours=24, sync_run_id="lease-success"
            )

        claim.assert_called_once()
        release.assert_called_once_with(claim.call_args.args[0], claim.call_args.args[1])
        api_client.get_fixture_statistics.assert_called_once_with(101)
        self.assertEqual(result["unavailable"], 1)
        self.assertEqual(result["api_calls"], 1)

    def test_xg_backfill_releases_lease_after_fetch_exception(self):
        api_client = Mock()
        api_client.request_count = 0
        api_client.get_fixture_statistics.side_effect = RuntimeError("network failure")
        rows = [
            {"fixture_id": 101, "date": "2026-11-21", "league_id": 61, "season": 2026}
        ]
        with (
            patch.object(full_sync_service, "client", api_client),
            patch.object(full_sync_service, "_priority_preemption_requested", return_value=False),
            patch.object(full_sync_service, "_daily_reserve_reached", return_value=False),
            patch.object(full_sync_service, "_xg_should_download", return_value=True),
            patch.object(full_sync_service.xg_service, "fixture_statistics_present", return_value=False),
            patch.object(full_sync_service.xg_service, "record_ingestion"),
            patch.object(full_sync_service.sync_registry, "get", return_value=None),
            patch.object(full_sync_service.sync_registry, "claim_download", return_value=True) as claim,
            patch.object(full_sync_service.sync_registry, "release_download_claim") as release,
            patch.object(full_sync_service.sync_registry, "mark"),
            patch.object(full_sync_service.time, "sleep"),
        ):
            result = full_sync_service._sync_xg_rows(
                rows, pause=0, retry_hours=24, sync_run_id="lease-error"
            )

        release.assert_called_once_with(claim.call_args.args[0], claim.call_args.args[1])
        self.assertEqual(result["errors"], ["fixture-statistics:101: network failure"])
        self.assertEqual(result["api_calls"], 1)

    def test_xg_backfill_stops_on_emergency_reserve_protection(self):
        api_client = Mock()
        api_client.request_count = 0
        api_client.last_rate_limit = {}
        api_client.get_fixture_statistics.side_effect = RuntimeError(
            "Appel API différé (xg_enrichment) : "
            "emergency_reserve_protected; réserve protégée=1063."
        )
        rows = [
            {
                "fixture_id": 101,
                "date": "2026-11-21",
                "league_id": 61,
                "season": 2026,
            },
            {
                "fixture_id": 102,
                "date": "2026-11-20",
                "league_id": 61,
                "season": 2026,
            },
        ]

        with (
            patch.object(full_sync_service, "client", api_client),
            patch.object(
                full_sync_service,
                "_priority_preemption_requested",
                return_value=False,
            ),
            patch.object(full_sync_service, "_daily_reserve_reached", return_value=False),
            patch.object(full_sync_service, "_xg_should_download", return_value=True),
            patch.object(
                full_sync_service.xg_service,
                "fixture_statistics_present",
                return_value=False,
            ),
            patch.object(full_sync_service.xg_service, "record_ingestion"),
            patch.object(full_sync_service.sync_registry, "get", return_value=None),
            patch.object(full_sync_service.sync_registry, "mark"),
            patch.object(full_sync_service.sync_registry, "claim_download", return_value=True),
            patch.object(full_sync_service.sync_registry, "release_download_claim") as release,
            patch.object(full_sync_service.time, "sleep"),
        ):
            result = full_sync_service._sync_xg_rows(
                rows, pause=0, retry_hours=24, sync_run_id="reserve-test"
            )

        self.assertTrue(result["quota_reached"])
        self.assertEqual(result["checkpoint"], "fixture-statistics:101")
        api_client.get_fixture_statistics.assert_called_once_with(101)
        release.assert_called_once()

    def test_old_unavailable_fixture_resource_is_not_requested_again(self):
        with patch.object(
            full_sync_service.sync_registry,
            "should_download",
            return_value=True,
        ) as should_download:
            due = full_sync_service._resource_should_download(
                {"fixture_id": 1, "date": "2020-01-01T12:00:00"},
                {"status": "unavailable"},
                "fixture-lineup:1",
                12,
                terminal_unavailable_after_hours=72,
            )

        self.assertFalse(due)
        should_download.assert_not_called()

    def test_running_fixture_resource_uses_lease_before_exhaustive_retry(self):
        now = datetime.datetime(2026, 11, 17, 12, 0, 0)
        fresh = {
            "status": "running",
            "updated_at": (now - datetime.timedelta(minutes=5)).isoformat(),
        }
        stale = {
            "status": "running",
            "updated_at": (now - datetime.timedelta(minutes=31)).isoformat(),
        }
        row = {"fixture_id": 1, "date": "2026-11-17T12:00:00"}

        with patch.object(sync_registry, "get", side_effect=[fresh, stale]):
            with patch.object(sync_registry, "_now", return_value=now):
                self.assertFalse(
                    full_sync_service._resource_should_download(
                        row, fresh, "fixture-lineup:1", 12
                    )
                )
                self.assertTrue(
                    full_sync_service._resource_should_download(
                        row, stale, "fixture-lineup:1", 12
                    )
                )

    def test_recent_core_scope_respects_refresh_ttl(self):
        now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
        with patch.object(
            full_sync_service.sync_registry,
            "get",
            return_value={
                "status": "complete",
                "updated_at": (now - datetime.timedelta(hours=2)).isoformat(),
            },
        ):
            self.assertFalse(
                full_sync_service._registry_refresh_due("core:61:2026", 6)
            )

    def test_manual_retry_supports_the_previous_service_signature(self):
        calls = []

        def legacy_start_full_sync():
            calls.append("started")
            return "legacy-job"

        with patch.object(
            data_management.background_jobs,
            "start_full_sync",
            legacy_start_full_sync,
        ):
            result = data_management._start_full_sync(resumed=True)

        self.assertEqual(result, "legacy-job")
        self.assertEqual(calls, ["started"])

    def test_pages_tolerate_the_previous_background_service_during_deploy(self):
        root = Path(__file__).resolve().parents[1]
        update_page = (root / "pages" / "data_management.py").read_text(
            encoding="utf-8"
        )
        shared_ui = (root / "components" / "ui.py").read_text(encoding="utf-8")

        self.assertIn(
            'getattr(background_jobs, "resume_pending_full_sync", None)',
            update_page,
        )
        self.assertIn(
            'getattr(background_jobs, "resume_pending_full_sync", None)',
            shared_ui,
        )
        self.assertIn('getattr(background_jobs, "full_sync_state", None)', update_page)

    def test_sync_registry_metadata_survives_database_roundtrip(self):
        test_engine = create_engine("sqlite://")
        with patch.object(sync_registry, "engine", test_engine):
            sync_registry.ensure_table()
            sync_registry.mark(
                "full-sync:exhaustive",
                "full_sync_control",
                "waiting_quota",
                metadata={"checkpoint": "fixture-detail:42", "attempt": 3},
            )
            state = sync_registry.get("full-sync:exhaustive")

        self.assertEqual(state["status"], "waiting_quota")
        self.assertEqual(state["metadata"]["checkpoint"], "fixture-detail:42")
        self.assertEqual(state["metadata"]["attempt"], 3)

    def test_full_sync_skips_when_remote_lease_is_contended(self):
        with (
            patch.object(background_jobs, "_jobs", {}),
            patch.object(background_jobs.threading, "Thread", _ImmediateThread),
            patch.object(background_jobs, "full_sync_state", return_value=None),
            patch.object(background_jobs.remote_sync_lease, "claim", return_value=False) as claim,
            patch.object(background_jobs.sync_registry, "claim_download") as local_claim,
            patch.object(background_jobs.remote_sync_lease, "renew") as renew,
            patch.object(background_jobs.remote_sync_lease, "release") as release,
            patch.object(background_jobs, "_archive_pre_match_predictions") as archive,
            patch.object(background_jobs, "api_quota_status") as quota,
            patch.object(full_sync_service, "run_full_sync") as run_full_sync,
        ):
            job_id = background_jobs.start_full_sync()
            job = next(job for job in background_jobs.list_jobs() if job["id"] == job_id)

        self.assertEqual(job["status"], "skipped")
        self.assertTrue(job["details"]["remote_lease_contended"])
        claim.assert_called_once()
        local_claim.assert_not_called()
        renew.assert_not_called()
        release.assert_not_called()
        archive.assert_not_called()
        quota.assert_not_called()
        run_full_sync.assert_not_called()

    def test_full_sync_skips_when_global_lease_is_owned_by_another_worker(self):
        with (
            patch.object(background_jobs, "_jobs", {}),
            patch.object(background_jobs.threading, "Thread", _ImmediateThread),
            patch.object(background_jobs, "full_sync_state", return_value=None),
            patch.object(background_jobs.remote_sync_lease, "claim", return_value=True),
            patch.object(
                background_jobs.sync_registry,
                "claim_download",
                return_value=False,
            ) as claim,
            patch.object(background_jobs.sync_registry, "renew_download_claim") as renew,
            patch.object(background_jobs.sync_registry, "release_download_claim") as release,
            patch.object(background_jobs.remote_sync_lease, "release") as remote_release,
            patch.object(background_jobs, "_archive_pre_match_predictions") as archive,
            patch.object(background_jobs, "api_quota_status") as quota,
            patch.object(full_sync_service, "run_full_sync") as run_full_sync,
        ):
            job_id = background_jobs.start_full_sync()
            job = next(job for job in background_jobs.list_jobs() if job["id"] == job_id)

        self.assertEqual(job["status"], "skipped")
        self.assertTrue(job["details"]["lease_contended"])
        claim.assert_called_once()
        renew.assert_not_called()
        release.assert_not_called()
        remote_release.assert_called_once()
        archive.assert_not_called()
        quota.assert_not_called()
        run_full_sync.assert_not_called()

    def test_full_sync_stops_when_remote_lease_renewal_fails(self):
        with (
            patch.object(background_jobs, "_jobs", {}),
            patch.object(background_jobs.threading, "Thread", _ImmediateThread),
            patch.object(background_jobs, "full_sync_state", return_value=None),
            patch.object(background_jobs.remote_sync_lease, "claim", return_value=True),
            patch.object(background_jobs.sync_registry, "claim_download", return_value=True),
            patch.object(background_jobs.remote_sync_lease, "renew", return_value=False) as remote_renew,
            patch.object(background_jobs.sync_registry, "renew_download_claim") as local_renew,
            patch.object(background_jobs.sync_registry, "release_download_claim") as local_release,
            patch.object(background_jobs.remote_sync_lease, "release") as remote_release,
            patch.object(background_jobs, "_archive_pre_match_predictions") as archive,
            patch.object(background_jobs, "api_quota_status") as quota,
            patch.object(background_jobs.sync_registry, "mark") as mark,
            patch.object(background_jobs.import_service, "record_update_log") as record_log,
            patch.object(full_sync_service, "run_full_sync") as run_full_sync,
        ):
            job_id = background_jobs.start_full_sync()
            job = next(job for job in background_jobs.list_jobs() if job["id"] == job_id)

        self.assertEqual(job["status"], "error")
        self.assertIn("Lease distante", job["error"])
        remote_renew.assert_called_once()
        local_renew.assert_not_called()
        local_release.assert_called_once()
        remote_release.assert_called_once()
        archive.assert_not_called()
        quota.assert_not_called()
        mark.assert_not_called()
        record_log.assert_not_called()
        run_full_sync.assert_not_called()

    def test_quota_releases_the_job_and_persists_the_resume_point(self):
        result = {
            "quota_reached": True,
            "checkpoint": "fixture-statistics:9",
            "errors": ["429 quota reached"],
            "partial": 0,
        }
        with (
            patch.object(background_jobs, "_jobs", {}),
            patch.object(background_jobs.threading, "Thread", _ImmediateThread),
            patch.object(background_jobs, "full_sync_state", return_value=None),
            patch.object(background_jobs.remote_sync_lease, "claim", return_value=True),
            patch.object(background_jobs.sync_registry, "claim_download", return_value=True),
            patch.object(background_jobs.remote_sync_lease, "renew", return_value=True),
            patch.object(background_jobs.sync_registry, "renew_download_claim", return_value=True),
            patch.object(background_jobs.sync_registry, "release_download_claim") as release,
            patch.object(background_jobs.remote_sync_lease, "release") as remote_release,
            patch.object(
                background_jobs,
                "api_quota_status",
                return_value={"verified": True, "remaining": 10, "current": 90, "limit": 100},
            ),
            patch.object(background_jobs, "_full_sync_retry_seconds", return_value=60),
            patch.object(background_jobs.sync_registry, "mark") as mark,
            patch.object(background_jobs.import_service, "record_update_log"),
            patch.object(
                full_sync_service,
                "run_full_sync",
                return_value=result,
            ) as run_full_sync,
        ):
            job_id = background_jobs.start_full_sync()
            job = next(job for job in background_jobs.list_jobs() if job["id"] == job_id)

        run_full_sync.assert_called_once()
        self.assertEqual(job["status"], "partial")
        self.assertIsNotNone(job["finished_at"])
        statuses = [item.args[2] for item in mark.call_args_list]
        self.assertEqual(statuses[-1], "waiting_quota")
        self.assertEqual(
            mark.call_args.kwargs["metadata"]["checkpoint"],
            "fixture-statistics:9",
        )
        release.assert_called_once()

    def test_exhausted_quota_preflight_defers_full_sync_without_data_calls(self):
        with (
            patch.object(background_jobs, "_jobs", {}),
            patch.object(background_jobs.threading, "Thread", _ImmediateThread),
            patch.object(background_jobs, "full_sync_state", return_value=None),
            patch.object(background_jobs.remote_sync_lease, "claim", return_value=True),
            patch.object(background_jobs.sync_registry, "claim_download", return_value=True),
            patch.object(background_jobs.remote_sync_lease, "renew", return_value=True),
            patch.object(background_jobs.sync_registry, "renew_download_claim", return_value=True),
            patch.object(background_jobs.sync_registry, "release_download_claim"),
            patch.object(background_jobs.remote_sync_lease, "release"),
            patch.object(
                background_jobs,
                "api_quota_status",
                return_value={
                    "verified": True,
                    "available": False,
                    "current": 100,
                    "limit": 100,
                    "remaining": 0,
                    "checked_at": "2026-11-15T12:00:00",
                },
            ),
            patch.object(background_jobs, "_full_sync_retry_seconds", return_value=60),
            patch.object(background_jobs.sync_registry, "mark") as mark,
            patch.object(background_jobs.import_service, "record_update_log"),
            patch.object(background_jobs, "_backup_after_data_job"),
            patch.object(full_sync_service, "run_full_sync") as run_full_sync,
        ):
            job_id = background_jobs.start_full_sync()
            job = next(job for job in background_jobs.list_jobs() if job["id"] == job_id)

        run_full_sync.assert_not_called()
        self.assertEqual(job["status"], "partial")
        self.assertEqual(job["quota_preflight"]["remaining"], 0)
        self.assertEqual(mark.call_args.args[2], "waiting_quota")

    def test_retry_delay_distinguishes_minute_and_daily_quotas(self):
        now = datetime.datetime(2026, 9, 7, 20, 30, tzinfo=datetime.UTC)
        with patch.dict("os.environ", {}, clear=False):
            with patch.dict("os.environ", {"FULL_SYNC_QUOTA_RETRY_SECONDS": ""}):
                minute = background_jobs._full_sync_retry_seconds(
                    {"errors": ["You have reached the request limit for the minute"]},
                    now=now,
                )
                daily = background_jobs._full_sync_retry_seconds(
                    {"errors": ["Daily request quota reached"]}, now=now
                )

        self.assertEqual(minute, 90)
        self.assertEqual(daily, 12_600)

    def test_interrupted_persistent_sync_is_resumed_after_restart(self):
        starter = Mock(return_value="new-job")
        past = (
            datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
            - datetime.timedelta(minutes=1)
        ).isoformat()
        with (
            patch.object(background_jobs, "data_job_running", return_value=False),
            patch.object(
                background_jobs,
                "full_sync_state",
                return_value={
                    "status": "waiting_quota",
                    "metadata": {"next_retry_at": past},
                },
            ),
            patch.object(background_jobs, "start_full_sync", starter),
        ):
            result = background_jobs.resume_pending_full_sync()

        self.assertEqual(result, "new-job")
        starter.assert_called_once_with(resumed=True)

    def test_due_full_sync_resumes_automatically_on_cloud(self):
        starter = Mock(return_value="resumed-job")
        past = (
            datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
            - datetime.timedelta(minutes=1)
        ).isoformat()
        with (
            patch.object(
                background_jobs, "_running_on_streamlit_cloud", return_value=True
            ),
            patch.dict("os.environ", {}, clear=True),
            patch.object(background_jobs, "data_job_running", return_value=False),
            patch.object(
                background_jobs,
                "cached_full_sync_state",
                return_value={
                    "status": "waiting_quota",
                    "metadata": {"next_retry_at": past},
                },
            ),
            patch.object(background_jobs, "start_full_sync", starter),
        ):
            result = background_jobs.resume_pending_full_sync_if_due()

        self.assertEqual(result, "resumed-job")
        starter.assert_called_once_with(resumed=True)

    def test_sync_resumes_after_due_time_without_status_confirmation(self):
        starter = Mock(return_value="resumed-job")
        past = (
            datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
            - datetime.timedelta(minutes=1)
        ).isoformat()
        with (
            patch.object(background_jobs, "data_job_running", return_value=False),
            patch.object(
                background_jobs,
                "full_sync_state",
                return_value={
                    "status": "waiting_quota",
                    "metadata": {"next_retry_at": past},
                },
            ),
            patch.object(background_jobs, "api_quota_status") as quota_status,
            patch.object(background_jobs, "start_full_sync", starter),
        ):
            result = background_jobs.resume_pending_full_sync()

        self.assertEqual(result, "resumed-job")
        starter.assert_called_once_with(resumed=True)
        quota_status.assert_not_called()

    def test_reserved_budget_does_not_restart_the_exhaustive_sync(self):
        starter = Mock()
        future = (
            datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
            + datetime.timedelta(hours=12)
        ).isoformat()
        with (
            patch.object(background_jobs, "data_job_running", return_value=False),
            patch.object(
                background_jobs,
                "full_sync_state",
                return_value={
                    "status": "waiting_quota",
                    "metadata": {
                        "next_retry_at": future,
                        "budget_reserved": True,
                        "daily_reserve": 500,
                    },
                },
            ),
            patch.object(background_jobs, "api_quota_status") as quota_status,
            patch.object(background_jobs, "start_full_sync", starter),
        ):
            result = background_jobs.resume_pending_full_sync()

        self.assertIsNone(result)
        starter.assert_not_called()
        quota_status.assert_not_called()

    def test_reserved_budget_restarts_at_due_time_without_status(self):
        starter = Mock(return_value="renewed-job")
        past = (
            datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
            - datetime.timedelta(minutes=1)
        ).isoformat()
        with (
            patch.object(background_jobs, "data_job_running", return_value=False),
            patch.object(
                background_jobs,
                "full_sync_state",
                return_value={
                    "status": "waiting_quota",
                    "metadata": {
                        "next_retry_at": past,
                        "budget_reserved": True,
                        "daily_reserve": 500,
                    },
                },
            ),
            patch.object(background_jobs, "api_quota_status") as quota_status,
            patch.object(background_jobs, "start_full_sync", starter),
        ):
            result = background_jobs.resume_pending_full_sync()

        self.assertEqual(result, "renewed-job")
        starter.assert_called_once_with(resumed=True)
        quota_status.assert_not_called()

    def test_due_sync_does_not_wait_for_an_exhausted_status_response(self):
        starter = Mock(return_value="attempted-job")
        past = (
            datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
            - datetime.timedelta(hours=1)
        ).isoformat()
        with (
            patch.object(background_jobs, "data_job_running", return_value=False),
            patch.object(
                background_jobs,
                "full_sync_state",
                return_value={
                    "status": "waiting_quota",
                    "metadata": {"next_retry_at": past},
                },
            ),
            patch.object(background_jobs, "api_quota_status") as quota_status,
            patch.object(background_jobs, "start_full_sync", starter),
        ):
            result = background_jobs.resume_pending_full_sync()

        self.assertEqual(result, "attempted-job")
        starter.assert_called_once_with(resumed=True)
        quota_status.assert_not_called()

    def test_full_sync_covers_past_and_future_matches(self):
        events = []
        progress_updates = []
        past = {
            "fixture_id": 1,
            "league_id": 61,
            "season": 2025,
            "date": "2026-01-01",
            "home_team_id": 10,
            "away_team_id": 20,
            "home_goals": 2,
            "away_goals": 1,
        }
        future = {
            "fixture_id": 2,
            "league_id": 61,
            "season": 2025,
            "date": "2099-01-01",
            "home_team_id": 10,
            "away_team_id": 20,
            "home_goals": None,
            "away_goals": None,
        }
        config = {
            "league_ids": [61],
            "start_season": 2024,
            "end_season": 2025,
            "recent_seasons": 1,
            "pause": 0,
            "max_retries": 1,
        }
        with ExitStack() as stack:
            stack.enter_context(patch.object(full_sync_service.import_service, "init_db"))
            stack.enter_context(patch.object(full_sync_service.sync_registry, "ensure_table"))
            mark = stack.enter_context(
                patch.object(
                    full_sync_service.sync_registry,
                    "mark",
                    side_effect=lambda key, *_args, **_kwargs: (
                        events.append(key) if key.endswith(":2") else None
                    ),
                )
            )
            stack.enter_context(patch.object(full_sync_service.sync_registry, "should_download", return_value=False))
            stack.enter_context(
                patch.object(full_sync_service.sync_registry, "get", return_value={"status": "unavailable"})
            )
            stack.enter_context(
                patch.object(full_sync_service.import_service, "get_auto_refresh_config", return_value=config)
            )
            stack.enter_context(
                patch.object(
                    full_sync_service,
                    "download_plan",
                    return_value={
                        "priority_order": [
                            {"key": "fixture-lineup", "priority": 5},
                            {"key": "fixture-detail", "priority": 15},
                            {"key": "fixture-prediction", "priority": 25},
                            {"key": "core", "priority": 50},
                            {"key": "fixture-statistics", "priority": 80},
                        ]
                    },
                )
            )
            stack.enter_context(
                patch.object(
                    full_sync_service.import_service,
                    "import_leagues_cautious",
                    side_effect=lambda _ids, seasons=None, progress_callback=None, **_kwargs: (
                        progress_callback(1, 2, "historical-core-test-1")
                        if int(seasons[0]) == 2024 and progress_callback
                        else None
                    ) or (
                        progress_callback(2, 2, "historical-core-test-2")
                        if int(seasons[0]) == 2024 and progress_callback
                        else None
                    ) or (
                        events.append(
                            "recent_core" if int(seasons[0]) == 2025 else f"core_{seasons[0]}"
                        ) or {}
                    ),
                )
            )
            stack.enter_context(
                patch.object(
                    full_sync_service,
                    "_missing_core_scopes",
                    side_effect=[[], [(61, 2024)], []],
                )
            )
            stack.enter_context(patch.object(full_sync_service, "_all_matches", return_value=[past, future]))
            stack.enter_context(patch.object(full_sync_service, "_in_progress_matches", return_value=[]))
            stack.enter_context(patch.object(full_sync_service, "_upcoming_matches", return_value=[future]))
            stack.enter_context(patch.object(full_sync_service, "_recent_fixture_ids", return_value=[1]))
            stack.enter_context(patch.object(full_sync_service, "_player_scopes", return_value=[]))
            stack.enter_context(patch.object(full_sync_service, "_fixture_details_present", return_value=True))
            stack.enter_context(patch.object(full_sync_service, "_lineup_present", return_value=True))
            stack.enter_context(
                patch.object(
                    full_sync_service,
                    "_prediction_present",
                    side_effect=lambda _fixture_id: (events.append("upcoming_prediction_check") or True),
                )
            )
            stack.enter_context(patch.object(full_sync_service, "_fixture_players_present", return_value=False))
            stack.enter_context(
                patch.object(
                    full_sync_service.lineup_service,
                    "sync_fixture_players",
                    return_value={"players": 0},
                )
            )
            sync_xg = stack.enter_context(
                patch.object(
                    full_sync_service,
                    "sync_historical_xg",
                    side_effect=lambda **_kwargs: (events.append("historical_xg") or {
                        "downloaded": 0,
                        "skipped": 1,
                        "unavailable": 0,
                        "errors": [],
                        "quota_reached": False,
                    }),
                )
            )
            stack.enter_context(patch.object(full_sync_service, "prediction_coverage", return_value={}))
            stack.enter_context(patch.object(full_sync_service.xg_service, "coverage", return_value={}))
            result = full_sync_service.run_full_sync(
                progress_callback=lambda current, total, label: progress_updates.append(
                    (current, label)
                )
            )

        marked_keys = [item.args[0] for item in mark.call_args_list]
        self.assertIn("fixture-detail:1", marked_keys)
        self.assertIn("fixture-detail:2", marked_keys)
        self.assertNotIn("fixture-lineup:1", marked_keys)
        self.assertIn("fixture-lineup:2", marked_keys)
        self.assertIn("fixture-prediction:2", marked_keys)
        sync_xg.assert_called_once()
        self.assertEqual(sync_xg.call_args.kwargs["league_ids"], [61])
        self.assertEqual(sync_xg.call_args.kwargs["seasons"], [2024, 2025])
        self.assertIsNone(sync_xg.call_args.kwargs["max_matches"])
        self.assertEqual(result["mode"], "exhaustive")
        self.assertIn("api_calls_by_family", result)
        self.assertIn("quota_status_by_family", result)
        self.assertIn("quota_metrics", result)
        self.assertIn("données principales", result["quota_status_by_family"])
        self.assertIn("requests_avoided_at_least", result)
        self.assertEqual(
            result["priority_dispatch_order"],
            ["fixture-lineup", "fixture-detail", "fixture-prediction"],
        )
        self.assertEqual(
            result["historical_dispatch_order"],
            ["core", "fixture-statistics"],
        )
        self.assertLess(events.index("recent_core"), events.index("upcoming_prediction_check"))
        self.assertLess(events.index("core_2024"), events.index("historical_xg"))
        core_callback_progress = [
            current for current, label in progress_updates
            if label.startswith("historical-core-test")
        ]
        self.assertEqual(core_callback_progress, [29, 33])
        progress_values = [current for current, _label in progress_updates]
        self.assertEqual(progress_values, sorted(progress_values))
        self.assertLess(events.index("fixture-lineup:2"), events.index("fixture-detail:2"))
        self.assertLess(events.index("fixture-detail:2"), events.index("fixture-prediction:2"))
        self.assertLess(events.index("upcoming_prediction_check"), events.index("historical_xg"))

    def test_xg_quota_stops_before_less_important_match_endpoints(self):
        played = {
            "fixture_id": 1,
            "league_id": 61,
            "season": 2025,
            "date": "2026-01-01",
            "home_team_id": 10,
            "away_team_id": 20,
            "home_goals": 2,
            "away_goals": 1,
        }
        config = {
            "league_ids": [61],
            "start_season": 2025,
            "end_season": 2025,
            "recent_seasons": 1,
            "pause": 0,
            "max_retries": 1,
        }
        with (
            patch.object(full_sync_service.import_service, "init_db"),
            patch.object(full_sync_service.sync_registry, "ensure_table"),
            patch.object(full_sync_service.sync_registry, "mark"),
            patch.object(full_sync_service, "_registry_refresh_due", return_value=True),
            patch.object(full_sync_service.import_service, "get_auto_refresh_config", return_value=config),
            patch.object(
                full_sync_service.import_service, "import_leagues_cautious", return_value={}
            ) as core_import,
            patch.object(full_sync_service, "_missing_core_scopes", return_value=[]),
            patch.object(full_sync_service, "_all_matches", return_value=[played]),
            patch.object(full_sync_service, "_upcoming_matches", return_value=[]),
            patch.object(full_sync_service, "_player_scopes", return_value=[]),
            patch.object(
                full_sync_service,
                "sync_historical_xg",
                return_value={
                    "downloaded": 7,
                    "skipped": 3,
                    "unavailable": 1,
                    "errors": ["fixture-statistics:99: daily quota"],
                    "quota_reached": True,
                    "checkpoint": "fixture-statistics:99",
                },
            ),
            patch.object(full_sync_service, "_fixture_details_present") as details,
            patch.object(full_sync_service.xg_service, "coverage", return_value={}),
        ):
            result = full_sync_service.run_full_sync()

        details.assert_not_called()
        core_import.assert_called_once()
        self.assertTrue(result["quota_reached"])
        self.assertEqual(result["checkpoint"], "fixture-statistics:99")

    def test_xg_budget_reserve_continues_with_current_data(self):
        config = {
            "league_ids": [61],
            "start_season": 2025,
            "end_season": 2025,
            "recent_seasons": 1,
            "pause": 0,
            "max_retries": 1,
        }
        with (
            patch.object(full_sync_service.import_service, "init_db"),
            patch.object(full_sync_service.sync_registry, "ensure_table"),
            patch.object(full_sync_service.sync_registry, "mark"),
            patch.object(
                full_sync_service.sync_registry,
                "get",
                return_value={
                    "status": "partial",
                    "metadata": {"checkpoint": "fixtures-page:61:2025:3"},
                },
            ),
            patch.object(full_sync_service, "download_plan", return_value={}),
            patch.object(
                full_sync_service.import_service,
                "get_auto_refresh_config",
                return_value=config,
            ),
            patch.object(
                full_sync_service,
                "sync_historical_xg",
                return_value={
                    "downloaded": 100,
                    "skipped": 0,
                    "unavailable": 0,
                    "errors": [],
                    "quota_reached": True,
                    "budget_reserved": True,
                    "checkpoint": "fixture-statistics:99",
                    "daily_reserve": 1500,
                },
            ),
            patch.object(
                full_sync_service,
                "_missing_core_scopes",
                side_effect=[[(61, 2025)], [], []],
            ),
            patch.object(full_sync_service, "_registry_refresh_due", return_value=False),
            patch.object(
                full_sync_service.import_service, "import_leagues_cautious", return_value={}
            ) as core_import,
            patch.object(full_sync_service, "_all_matches", return_value=[]),
            patch.object(full_sync_service, "_upcoming_matches", return_value=[]),
            patch.object(full_sync_service, "_recent_fixture_ids", return_value=[]),
            patch.object(full_sync_service, "_player_scopes", return_value=[]),
            patch.object(full_sync_service, "prediction_coverage", return_value={}),
            patch.object(full_sync_service.xg_service, "coverage", return_value={}),
        ):
            result = full_sync_service.run_full_sync()

        core_import.assert_called_once()
        self.assertEqual(
            core_import.call_args.kwargs["resume_checkpoint"],
            "fixtures-page:61:2025:3",
        )
        self.assertFalse(result["quota_reached"])
        self.assertTrue(result["xg_budget_reserved"])
        self.assertEqual(result["xg_resume_checkpoint"], "fixture-statistics:99")





def test_persisted_core_checkpoint_is_reusable_after_error_status():
    state = {
        "status": "error",
        "metadata": {"checkpoint": "fixtures-page:61:2026:4"},
    }
    with patch.object(sync_registry, "get", return_value=state):
        assert full_sync_service._persisted_core_resume_checkpoint(61, 2026) == (
            "fixtures-page:61:2026:4"
        )


def test_resumable_core_scopes_include_checkpointed_old_seasons():
    state = {
        "status": "partial",
        "metadata": {"checkpoint": "standings:39:2018"},
    }
    with patch.object(
        sync_registry,
        "get",
        side_effect=lambda key: state if key == "core:39:2018" else None,
    ):
        assert full_sync_service._resumable_core_scopes([61, 39], [2018, 2026]) == {
            (39, 2018)
        }


def test_persisted_core_checkpoint_rejects_invalid_scope_and_page():
    invalid = [
        {"status": "error", "metadata": {"checkpoint": "fixtures-page:61:2026:0"}},
        {"status": "error", "metadata": {"checkpoint": "fixtures-page:62:2026:3"}},
        {"status": "complete", "metadata": {"checkpoint": "fixtures-page:61:2026:3"}},
    ]
    for state in invalid:
        with patch.object(sync_registry, "get", return_value=state):
            assert full_sync_service._persisted_core_resume_checkpoint(61, 2026) is None


if __name__ == "__main__":
    unittest.main()
