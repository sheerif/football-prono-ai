import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, text

from services import api_response_store


class ApiResponseStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "archive.db"
        self.engine = create_engine(f"sqlite:///{database_path}")
        self.engine_patch = patch.object(api_response_store, "engine", self.engine)
        self.engine_patch.start()
        api_response_store._table_ready = False

    def tearDown(self):
        api_response_store._table_ready = False
        self.engine_patch.stop()
        self.engine.dispose()
        self.temp_dir.cleanup()

    def test_archive_preserves_payload_request_and_quota_trace(self):
        payload = {"response": [{"fixture": {"id": 1552751}}], "errors": []}

        row_id = api_response_store.archive_response(
            endpoint="/fixtures/statistics",
            params={"fixture": 1552751},
            payload=payload,
            response_headers={
                "x-ratelimit-requests-limit": "7500",
                "x-ratelimit-requests-remaining": "7499",
                "X-RateLimit-Limit": "10",
                "X-RateLimit-Remaining": "9",
            },
            http_status=200,
        )

        with self.engine.connect() as conn:
            row = conn.execute(
                text("SELECT * FROM api_response_archive WHERE id = :id"),
                {"id": row_id},
            ).mappings().one()

        self.assertEqual(row["endpoint"], "/fixtures/statistics")
        self.assertEqual(json.loads(row["params_json"]), {"fixture": 1552751})
        self.assertEqual(json.loads(row["response_json"]), payload)
        self.assertEqual(row["http_status"], 200)
        self.assertEqual(row["daily_limit"], 7500)
        self.assertEqual(row["daily_remaining"], 7499)
        self.assertEqual(row["minute_limit"], 10)
        self.assertEqual(row["minute_remaining"], 9)
        self.assertEqual(len(row["request_sha256"]), 64)
        self.assertEqual(len(row["response_sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
