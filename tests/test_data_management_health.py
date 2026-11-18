import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, text

from pages import data_management


class DatabaseHealthTests(unittest.TestCase):
    def test_informational_timestamp_formats_paris_time_without_ui_module(self):
        value = data_management._format_informational_timestamp(
            "2026-11-18T10:00:00Z", label="État vérifié"
        )
        self.assertEqual(value, "État vérifié le 18/11/2026 à 11:00 (heure de Paris).")

    def test_informational_timestamp_ignores_invalid_values(self):
        self.assertEqual(data_management._format_informational_timestamp("not-a-date"), "")
        self.assertEqual(data_management._format_informational_timestamp(None), "")

    def test_quota_notice_uses_same_progress_as_job_progress_bar(self):
        job = {"progress": 0.70}
        metadata = {"progress": 1, "quota_kind": "daily"}
        normalized = data_management._quota_metadata_for_job(job, metadata)
        self.assertEqual(normalized["progress"], 70.0)
        self.assertEqual(metadata["progress"], 1)
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
