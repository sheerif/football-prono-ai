import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, text

from pages import data_management


class DatabaseHealthTests(unittest.TestCase):
    def test_database_health_selects_main_when_sqlite_has_attached_databases(self):
        test_engine = create_engine("sqlite://")
        try:
            with test_engine.begin() as connection:
                connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS auxiliary")
                connection.exec_driver_sql(
                    "CREATE TABLE update_log (id INTEGER PRIMARY KEY, finished_at TEXT)"
                )
                connection.execute(
                    text("INSERT INTO update_log (finished_at) VALUES ('2026-11-15')")
                )

            with patch.object(data_management, "engine", test_engine):
                health = data_management._database_health()

            self.assertEqual(health["database_file"], "")
            self.assertEqual(health["journal_mode"], "MEMORY")
            self.assertEqual(health["page_size"], 4096)
            self.assertEqual(health["latest_update"], "2026-11-15")
        finally:
            test_engine.dispose()


if __name__ == "__main__":
    unittest.main()
