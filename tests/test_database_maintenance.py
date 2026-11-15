import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine

from services import database_maintenance


class DatabaseMaintenanceTests(unittest.TestCase):
    def test_runs_optimize_and_checkpoint_without_unneeded_vacuum(self):
        with tempfile.TemporaryDirectory() as directory:
            database_engine = create_engine(
                f"sqlite:///{Path(directory) / 'compact.db'}"
            )
            with database_engine.begin() as connection:
                connection.exec_driver_sql("CREATE TABLE sample (value TEXT)")
                connection.exec_driver_sql("INSERT INTO sample VALUES ('kept')")

            result = database_maintenance.maintain_database(
                database_engine=database_engine
            )
            database_engine.dispose()

        self.assertEqual(result["status"], "maintained")
        self.assertFalse(result["vacuumed"])
        self.assertEqual(len(result["checkpoint"]), 3)

    def test_compacts_when_free_pages_cross_configured_threshold(self):
        with tempfile.TemporaryDirectory() as directory:
            database_engine = create_engine(
                f"sqlite:///{Path(directory) / 'fragmented.db'}"
            )
            with database_engine.begin() as connection:
                connection.exec_driver_sql("CREATE TABLE sample (value TEXT)")
                connection.exec_driver_sql(
                    "WITH RECURSIVE rows(value) AS ("
                    "SELECT 1 UNION ALL SELECT value + 1 FROM rows WHERE value < 300"
                    ") INSERT INTO sample SELECT printf('%01000d', value) FROM rows"
                )
                connection.exec_driver_sql("DELETE FROM sample")

            with patch.object(database_maintenance, "_VACUUM_MIN_FREE_PAGES", 1), patch.object(
                database_maintenance, "_VACUUM_FREE_RATIO", 0
            ):
                result = database_maintenance.maintain_database(
                    database_engine=database_engine
                )
            database_engine.dispose()

        self.assertTrue(result["vacuumed"])
        self.assertEqual(result["free_pages_after"], 0)
        self.assertGreater(result["reclaimed_bytes"], 0)


if __name__ == "__main__":
    unittest.main()
