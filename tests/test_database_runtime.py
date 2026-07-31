import unittest
from unittest.mock import Mock, patch

from database import models
from database.database import engine
from services import background_jobs, import_service


class DatabaseRuntimeTests(unittest.TestCase):
    def test_sqlite_connections_enable_integrity_and_lock_protection(self):
        if engine.dialect.name != "sqlite":
            self.skipTest("SQLite-specific runtime settings")
        with engine.connect() as connection:
            foreign_keys = connection.exec_driver_sql(
                "PRAGMA foreign_keys"
            ).scalar_one()
            busy_timeout = connection.exec_driver_sql(
                "PRAGMA busy_timeout"
            ).scalar_one()
            journal_mode = connection.exec_driver_sql(
                "PRAGMA journal_mode"
            ).scalar_one()
        self.assertEqual(foreign_keys, 1)
        self.assertGreaterEqual(busy_timeout, 30_000)
        self.assertEqual(str(journal_mode).casefold(), "wal")

    def test_performance_indexes_are_declared_in_metadata(self):
        declared = {
            index.name
            for table in models.Base.metadata.sorted_tables
            for index in table.indexes
        }
        self.assertTrue(
            {
                "ix_teams_league_id",
                "ix_matches_league_season_date",
                "ix_matches_home_team_date",
                "ix_matches_away_team_date",
                "ix_matches_date_scores",
                "ix_player_statistics_league_season_team",
            }.issubset(declared)
        )

    def test_only_one_data_job_can_be_registered_at_a_time(self):
        with patch.object(background_jobs, "_jobs", {}):
            first_id, first_created = background_jobs._create_unique_data_job(
                "manual_import",
                "Premier import",
            )
            second_id, second_created = background_jobs._create_unique_data_job(
                "full_sync",
                "Deuxième import",
            )
            self.assertTrue(first_created)
            self.assertFalse(second_created)
            self.assertEqual(first_id, second_id)
            self.assertTrue(background_jobs.data_job_running())

    def test_import_helpers_leave_transaction_control_to_the_batch(self):
        session = Mock()
        session.get.return_value = None

        import_service._get_or_create_team(
            session,
            {"id": 85, "name": "Test FC"},
            league_id=61,
        )
        import_service._save_match(
            session,
            {
                "fixture": {
                    "id": 12345,
                    "date": "2026-08-01T18:00:00Z",
                    "status": {"long": "Not Started"},
                },
                "teams": {
                    "home": {"id": 85},
                    "away": {"id": 86},
                },
                "goals": {"home": None, "away": None},
            },
            league_id=61,
            season=2026,
        )

        self.assertEqual(session.add.call_count, 2)
        session.commit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
