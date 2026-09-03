import unittest
from unittest.mock import patch

from services import background_jobs, import_service


class _ImmediateThread:
    def __init__(self, target, **_kwargs):
        self.target = target

    def start(self):
        self.target()


class BackgroundProgressTests(unittest.TestCase):
    def test_phase_progress_is_mapped_inside_the_global_range(self):
        with patch.object(background_jobs, "_jobs", {}):
            job_id = background_jobs._create_job("startup_updates", "Démarrage")
            background_jobs._phase_progress(
                job_id,
                current=1,
                total=2,
                start=0.50,
                end=0.98,
                label="Saison vérifiée",
            )
            job = background_jobs.list_jobs()[0]

        self.assertAlmostEqual(job["progress"], 0.74)
        self.assertEqual(job["message"], "74 % — Saison vérifiée")

    def test_startup_job_forwards_detailed_progress_and_finishes(self):
        def refresh_current(*, progress_callback):
            progress_callback(1, 2, "Championnat 1 vérifié")
            progress_callback(2, 2, "Championnat 2 vérifié")
            return {"ran": False, "reason": "Test"}

        def refresh_history(*, progress_callback):
            progress_callback(1, 4, "Saison 1 vérifiée")
            progress_callback(4, 4, "Historique vérifié")
            return {"ran": False, "reason": "Test"}

        with (
            patch.object(background_jobs, "_jobs", {}),
            patch.object(background_jobs, "_startup_started", False),
            patch.object(background_jobs.threading, "Thread", _ImmediateThread),
            patch.object(background_jobs.import_service, "init_db"),
            patch.object(
                background_jobs.import_service,
                "refresh_current_competitions_on_connection",
                side_effect=refresh_current,
            ),
            patch.object(
                background_jobs.import_service,
                "auto_refresh_if_due",
                side_effect=refresh_history,
            ),
            patch.object(background_jobs.import_service, "record_update_result"),
        ):
            job_id = background_jobs.start_startup_updates_once()
            job = next(job for job in background_jobs.list_jobs() if job["id"] == job_id)

        self.assertEqual(job["status"], "done")
        self.assertEqual(job["progress"], 1.0)
        self.assertEqual(job["message"], "Mises à jour de démarrage terminées")

    def test_historical_subphases_never_move_progress_backwards(self):
        updates = []
        config = {
            "enabled": True,
            "interval_minutes": 360,
            "league_ids": [61],
            "start_season": 2024,
            "end_season": 2025,
            "recent_seasons": 1,
            "pause": 0,
            "max_retries": 1,
        }

        def audit(_config, *, progress_callback):
            progress_callback(0, 2, "Audit lancé")
            progress_callback(2, 2, "Audit terminé")
            return {"accessible": [2024, 2025], "unavailable": []}

        def import_history(*_args, progress_callback, **_kwargs):
            progress_callback(0, 6, "Import lancé")
            progress_callback(3, 6, "Import à mi-parcours")
            progress_callback(6, 6, "Import terminé")

        with (
            patch.object(import_service, "get_auto_refresh_config", return_value=config),
            patch.object(import_service, "_last_refresh_is_recent", return_value=False),
            patch.object(
                import_service,
                "audit_configured_season_access",
                side_effect=audit,
            ),
            patch.object(
                import_service,
                "import_leagues_cautious",
                side_effect=import_history,
            ),
            patch.object(import_service, "_set_sync_value"),
        ):
            import_service.auto_refresh_if_due(
                progress_callback=lambda current, total, _label: updates.append(
                    current / total
                )
            )

        self.assertEqual(updates, sorted(updates))
        self.assertEqual(updates[-1], 1.0)


if __name__ == "__main__":
    unittest.main()
