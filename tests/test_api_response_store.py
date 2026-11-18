import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, text

from services import api_response_store


class ApiResponseStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.engine = create_engine(
            f"sqlite:///{Path(self.temp_dir.name) / 'request-state.db'}"
        )
        self.engine_patch = patch.object(api_response_store, "engine", self.engine)
        self.engine_patch.start()
        api_response_store._table_ready = False

    def tearDown(self):
        api_response_store._table_ready = False
        self.engine_patch.stop()
        self.engine.dispose()
        self.temp_dir.cleanup()

    def test_refresh_replaces_previous_state_without_storing_payload(self):
        first = {"response": [{"fixture": {"id": 1552751, "goals": 1}}], "errors": []}
        latest = {"response": [{"fixture": {"id": 1552751, "goals": 2}}], "errors": []}
        headers = {
            "x-ratelimit-requests-limit": "7500",
            "x-ratelimit-requests-remaining": "7499",
            "X-RateLimit-Limit": "10",
            "X-RateLimit-Remaining": "9",
        }

        api_response_store.archive_response(
            "/fixtures/statistics", {"fixture": 1552751}, first, headers, 200
        )
        api_response_store.archive_response(
            "/fixtures/statistics", {"fixture": 1552751}, latest, headers, 200
        )

        with self.engine.connect() as conn:
            rows = conn.execute(text("SELECT * FROM api_request_state")).mappings().all()

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["endpoint"], "/fixtures/statistics")
        self.assertEqual(row["http_status"], 200)
        self.assertEqual(row["daily_limit"], 7500)
        self.assertEqual(row["daily_remaining"], 7499)
        self.assertEqual(row["minute_limit"], 10)
        self.assertEqual(row["minute_remaining"], 9)
        self.assertEqual(
            row["response_sha256"],
            hashlib.sha256(
                api_response_store._json(latest).encode("utf-8")
            ).hexdigest(),
        )
        self.assertNotIn("response_json", row)
        self.assertNotIn("params_json", row)

    def test_different_request_parameters_have_separate_current_states(self):
        api_response_store.archive_response("/fixtures", {"id": 1}, {"response": []})
        api_response_store.archive_response("/fixtures", {"id": 2}, {"response": []})
        with self.engine.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM api_request_state")).scalar_one()
        self.assertEqual(count, 2)

    def test_legacy_archive_is_compacted_to_latest_row_per_request(self):
        with self.engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE api_response_archive (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    endpoint TEXT NOT NULL,
                    params_json TEXT NOT NULL,
                    request_sha256 TEXT NOT NULL,
                    response_sha256 TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    http_status INTEGER,
                    daily_limit INTEGER,
                    daily_remaining INTEGER,
                    minute_limit INTEGER,
                    minute_remaining INTEGER,
                    retrieved_at TEXT NOT NULL
                )
            """))
            conn.execute(text("""
                INSERT INTO api_response_archive (
                    endpoint, params_json, request_sha256, response_sha256,
                    response_json, http_status, retrieved_at
                ) VALUES
                    ('/fixtures', '{}', 'same-request', 'old-hash', :old_payload, 200, '2026-01-01'),
                    ('/fixtures', '{}', 'same-request', 'new-hash', :new_payload, 200, '2026-01-02')
            """), {"old_payload": '{"old":true}', "new_payload": '{"new":true}'})

        api_response_store.ensure_table()

        with self.engine.connect() as conn:
            row = conn.execute(
                text("SELECT * FROM api_request_state WHERE request_sha256='same-request'")
            ).mappings().one()
            self.assertFalse(
                conn.execute(text(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='api_response_archive'"
                )).first()
            )

        self.assertEqual(row["response_sha256"], "new-hash")
        self.assertEqual(row["retrieved_at"], "2026-01-02")


if __name__ == "__main__":
    unittest.main()
