import datetime
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from sqlalchemy import create_engine

from services import background_jobs, full_sync_service, sync_registry


class _ImmediateThread:
    def __init__(self, target, **_kwargs):
        self.target = target

    def start(self):
        self.target()


class ExhaustiveSyncTests(unittest.TestCase):
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

    def test_quota_waits_then_resumes_without_losing_the_job(self):
        wait = Mock()
        first = {
            "quota_reached": True,
            "checkpoint": "fixture-statistics:9",
            "errors": ["429 quota reached"],
            "partial": 0,
        }
        second = {"quota_reached": False, "errors": [], "partial": 0}
        with (
            patch.object(background_jobs, "_jobs", {}),
            patch.object(background_jobs.threading, "Thread", _ImmediateThread),
            patch.object(background_jobs.threading, "Event") as event,
            patch.object(background_jobs, "_full_sync_retry_seconds", return_value=60),
            patch.object(background_jobs.sync_registry, "mark") as mark,
            patch.object(background_jobs.import_service, "record_update_log"),
            patch.object(
                full_sync_service,
                "run_full_sync",
                side_effect=[first, second],
            ) as run_full_sync,
        ):
            event.return_value.wait = wait
            job_id = background_jobs.start_full_sync()
            job = next(job for job in background_jobs.list_jobs() if job["id"] == job_id)

        self.assertEqual(run_full_sync.call_count, 2)
        wait.assert_called_once_with(60)
        self.assertEqual(job["status"], "done")
        statuses = [item.args[2] for item in mark.call_args_list]
        self.assertIn("waiting_quota", statuses)
        self.assertEqual(statuses[-1], "complete")

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
            patch.object(full_sync_service.import_service, "get_auto_refresh_config", return_value=config),
            patch.object(full_sync_service.import_service, "import_leagues_cautious"),
            patch.object(full_sync_service, "_missing_core_scopes", return_value=[]),
            patch.object(full_sync_service, "_all_matches", return_value=[past, future]),
            patch.object(full_sync_service, "_upcoming_matches", return_value=[future]),
            patch.object(full_sync_service, "_player_scopes", return_value=[]),
            patch.object(full_sync_service, "_fixture_details_present", return_value=True),
            patch.object(full_sync_service, "_lineup_present", return_value=True),
            patch.object(full_sync_service, "_prediction_present", return_value=True),
            patch.object(
                full_sync_service,
                "_sync_xg_rows",
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
        self.assertIn("fixture-lineup:1", marked_keys)
        self.assertIn("fixture-lineup:2", marked_keys)
        self.assertIn("fixture-prediction:2", marked_keys)
        sync_xg.assert_called_once()
        self.assertEqual(sync_xg.call_args.args[0][0]["fixture_id"], 1)
        self.assertEqual(result["mode"], "exhaustive")


if __name__ == "__main__":
    unittest.main()
