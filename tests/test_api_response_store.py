import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, inspect, text

from services import api_response_store


class ApiResponseStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self.temp_dir.name) / 'request-state.db'}")
        self.patch = patch.object(api_response_store, "engine", self.engine)
        self.patch.start()
        api_response_store._table_ready = False

    def tearDown(self):
        api_response_store._table_ready = False
        self.patch.stop()
        self.engine.dispose()
        self.temp_dir.cleanup()

    def test_refresh_replaces_state_without_payload(self):
        latest = {"response": [{"fixture": {"id": 1, "goals": 2}}]}
        api_response_store.archive_response("/fixtures", {"id": 1}, {"goals": 1})
        api_response_store.archive_response("/fixtures", {"id": 1}, latest, {
            "x-ratelimit-requests-limit": "7500",
            "x-ratelimit-requests-remaining": "7499",
            "X-RateLimit-Limit": "10", "X-RateLimit-Remaining": "9",
        }, 200)
        with self.engine.connect() as conn:
            rows = conn.execute(text("SELECT * FROM api_request_state")).mappings().all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["response_sha256"], hashlib.sha256(
            api_response_store._json(latest).encode("utf-8")).hexdigest())
        self.assertEqual(rows[0]["daily_remaining"], 7499)
        self.assertNotIn("response_json", rows[0])
        self.assertNotIn("params_json", rows[0])

    def test_different_parameters_keep_separate_states(self):
        api_response_store.archive_response("/fixtures", {"id": 1}, {})
        api_response_store.archive_response("/fixtures", {"id": 2}, {})
        with self.engine.connect() as conn:
            self.assertEqual(conn.execute(text("SELECT COUNT(*) FROM api_request_state")).scalar_one(), 2)

    def test_migration_keeps_only_latest_state_and_then_drops_archive(self):
        with self.engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE api_response_archive (
                    id INTEGER PRIMARY KEY, endpoint TEXT NOT NULL, request_sha256 TEXT NOT NULL,
                    response_sha256 TEXT NOT NULL, http_status INTEGER, daily_limit INTEGER,
                    daily_remaining INTEGER, minute_limit INTEGER, minute_remaining INTEGER,
                    retrieved_at TEXT NOT NULL
                )
            """))
            conn.execute(text("""
                INSERT INTO api_response_archive
                (id, endpoint, request_sha256, response_sha256, retrieved_at)
                VALUES (1, '/fixtures', 'same', 'old', '2026-01-01'),
                       (2, '/fixtures', 'same', 'new', '2026-01-02'),
                       (3, '/teams', 'other', 'team', '2026-01-03')
            """))
        api_response_store.ensure_table()
        with self.engine.connect() as conn:
            rows = conn.execute(text("SELECT * FROM api_request_state ORDER BY request_sha256")).mappings().all()
            self.assertFalse(inspect(conn).has_table("api_response_archive"))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["response_sha256"], "new")
        self.assertEqual(rows[1]["retrieved_at"], "2026-01-02")

    def test_incomplete_legacy_schema_aborts_without_dropping_archive(self):
        with self.engine.begin() as conn:
            conn.execute(text("CREATE TABLE api_response_archive (id INTEGER PRIMARY KEY, endpoint TEXT)"))
        with self.assertRaisesRegex(RuntimeError, "schéma historique incomplet"):
            api_response_store.ensure_table()
        with self.engine.connect() as conn:
            self.assertTrue(inspect(conn).has_table("api_response_archive"))
            self.assertFalse(inspect(conn).has_table("api_request_state"))

    def test_invalid_legacy_rows_abort_without_dropping_archive(self):
        with self.engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE api_response_archive (
                    id INTEGER PRIMARY KEY, endpoint TEXT, request_sha256 TEXT,
                    response_sha256 TEXT, http_status INTEGER, daily_limit INTEGER,
                    daily_remaining INTEGER, minute_limit INTEGER, minute_remaining INTEGER,
                    retrieved_at TEXT
                )
            """))
            conn.execute(text("""
                INSERT INTO api_response_archive
                (id, endpoint, request_sha256, response_sha256, retrieved_at)
                VALUES (1, '/fixtures', NULL, 'hash', '2026-01-01')
            """))
        with self.assertRaisesRegex(RuntimeError, "ligne"):
            api_response_store.ensure_table()
        with self.engine.connect() as conn:
            self.assertTrue(inspect(conn).has_table("api_response_archive"))
            self.assertFalse(inspect(conn).has_table("api_request_state"))


if __name__ == "__main__":
    unittest.main()
