import datetime
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
            "start_season": 2025,
            "end_season": 2025,
            "recent_seasons": 1,
            "pause": 0,
            "max_retries": 1,
        }
        with (
            patch.object(full_sync_service.import_service, "init_db"),
            patch.object(full_sync_service.sync_registry, "ensure_table"),
            patch.object(full_sync_service.sync_registry, "mark") as mark,
            patch.object(full_sync_service.sync_registry, "should_download", return_value=False),
            patch.object(
                full_sync_service.sync_registry,
                "get",
                return_value={"status": "unavailable"},
            ),
            patch.object(full_sync_service.import_service, "get_auto_refresh_config", return_value=config),
            patch.object(full_sync_service.import_service, "import_leagues_cautious"),
            patch.object(full_sync_service, "_missing_core_scopes", return_value=[]),
            patch.object(full_sync_service, "_all_matches", return_value=[past, future]),
            patch.object(full_sync_service, "_upcoming_matches", return_value=[future]),
            patch.object(full_sync_service, "_recent_fixture_ids", return_value=[1]),
            patch.object(full_sync_service, "_player_scopes", return_value=[]),
            patch.object(full_sync_service, "_fixture_details_present", return_value=True),
            patch.object(full_sync_service, "_lineup_present", return_value=True),
            patch.object(full_sync_service, "_prediction_present", return_value=True),
            patch.object(full_sync_service, "_fixture_players_present", return_value=False),
            patch.object(
                full_sync_service,
                "sync_historical_xg",
                return_value={
                    "downloaded": 0,
                    "skipped": 1,
                    "unavailable": 0,
                    "errors": [],
                    "quota_reached": False,
                },
            ) as sync_xg,
            patch.object(full_sync_service, "prediction_coverage", return_value={}),
            patch.object(full_sync_service.xg_service, "coverage", return_value={}),
        ):
            result = full_sync_service.run_full_sync()

        marked_keys = [item.args[0] for item in mark.call_args_list]
        self.assertIn("fixture-detail:1", marked_keys)
        self.assertIn("fixture-detail:2", marked_keys)
        self.assertNotIn("fixture-lineup:1", marked_keys)
        self.assertIn("fixture-lineup:2", marked_keys)
        self.assertIn("fixture-prediction:2", marked_keys)
        sync_xg.assert_called_once()
        self.assertEqual(sync_xg.call_args.kwargs["league_ids"], [61])
        self.assertEqual(sync_xg.call_args.kwargs["seasons"], [2025])
        self.assertIsNone(sync_xg.call_args.kwargs["max_matches"])
        self.assertEqual(result["mode"], "exhaustive")
        self.assertIn("api_calls_by_family", result)
        self.assertIn("requests_avoided_at_least", result)

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
            patch.object(full_sync_service.import_service, "get_auto_refresh_config", return_value=config),
            patch.object(
                full_sync_service.import_service, "import_leagues_cautious"
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
        core_import.assert_not_called()
        self.assertTrue(result["quota_reached"])
        self.assertEqual(result["checkpoint"], "fixture-statistics:99")


if __name__ == "__main__":
    unittest.main()
