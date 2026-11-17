import datetime as dt
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, text

from services import data_reliability


class DataReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self.temp_dir.name) / 'reliability.db'}")
        self.patch = patch.object(data_reliability, "engine", self.engine)
        self.patch.start()
        with self.engine.begin() as conn:
            for table in ("leagues", "teams", "matches", "standings"):
                conn.execute(text(
                    f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, updated_at TEXT)"
                ))
                conn.execute(text(
                    f"INSERT INTO {table} (updated_at) VALUES ('2026-11-17T11:00:00+00:00')"
                ))
            conn.execute(text("""
                CREATE TABLE api_response_archive (
                    id INTEGER PRIMARY KEY, response_json TEXT NOT NULL,
                    response_sha256 TEXT NOT NULL, http_status INTEGER,
                    retrieved_at TEXT NOT NULL
                )
            """))
            payload = json.dumps({"response": []}, separators=(",", ":"))
            digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            conn.execute(text("""
                INSERT INTO api_response_archive
                (response_json, response_sha256, http_status, retrieved_at)
                VALUES (:payload, :digest, 200, '2026-11-17T11:00:00+00:00')
            """), {"payload": payload, "digest": digest})
            conn.execute(text("""
                CREATE TABLE update_log (id INTEGER PRIMARY KEY, finished_at TEXT)
            """))
            conn.execute(text("""
                INSERT INTO update_log (finished_at) VALUES ('2026-11-17T11:00:00+00:00')
            """))

    def tearDown(self):
        self.patch.stop()
        self.engine.dispose()
        self.temp_dir.cleanup()

    def test_snapshot_reports_counts_and_verified_archive(self):
        now = dt.datetime(2026, 11, 17, 12, tzinfo=dt.timezone.utc)
        snapshot = data_reliability.get_reliability_snapshot(now=now)
        self.assertEqual(snapshot["database"]["status"], "healthy")
        self.assertEqual([table["count"] for table in snapshot["tables"]], [1, 1, 1, 1])
        self.assertEqual(snapshot["api_archive"]["valid"], 1)
        self.assertEqual(snapshot["api_archive"]["invalid"], 0)
        self.assertEqual(snapshot["overall_status"], "healthy")

    def test_archive_tampering_is_critical(self):
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE api_response_archive SET response_sha256 = 'bad'"))
        snapshot = data_reliability.get_reliability_snapshot(
            now=dt.datetime(2026, 11, 17, 12, tzinfo=dt.timezone.utc)
        )
        self.assertEqual(snapshot["api_archive"]["status"], "critical")
        self.assertEqual(snapshot["overall_status"], "critical")

    def test_empty_table_is_warning_not_unknown_zero(self):
        with self.engine.begin() as conn:
            conn.execute(text("DELETE FROM teams"))
        snapshot = data_reliability.get_reliability_snapshot(
            now=dt.datetime(2026, 11, 17, 12, tzinfo=dt.timezone.utc)
        )
        teams = next(table for table in snapshot["tables"] if table["name"] == "teams")
        self.assertEqual(teams["count"], 0)
        self.assertEqual(teams["status"], "warning")

    def test_database_error_is_explicit_and_not_reported_as_zero(self):
        with patch.object(data_reliability, "engine") as broken:
            broken.connect.side_effect = RuntimeError("database unavailable")
            snapshot = data_reliability.get_reliability_snapshot()
        self.assertEqual(snapshot["database"]["status"], "critical")
        self.assertEqual(snapshot["overall_status"], "critical")
        self.assertIsNone(snapshot["tables"][0]["count"])


if __name__ == "__main__":
    unittest.main()
