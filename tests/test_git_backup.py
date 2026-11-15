import base64
import gzip
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine

from services import git_backup


class GitBackupTests(unittest.TestCase):
    def test_uploads_a_consistent_compressed_sqlite_copy_to_rolling_branch(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "source.db"
            database_engine = create_engine(f"sqlite:///{database_path}")
            with database_engine.begin() as connection:
                connection.exec_driver_sql("CREATE TABLE sample (value TEXT)")
                connection.exec_driver_sql("INSERT INTO sample VALUES ('saved')")

            calls = []

            def request(method, endpoint, token, payload=None):
                calls.append((method, endpoint, token, payload))
                if endpoint.endswith("/git/ref/heads/database-backup"):
                    raise RuntimeError("GitHub API (404) : ref not found")
                if endpoint.endswith("/git/blobs"):
                    compressed = base64.b64decode(payload["content"])
                    snapshot_path = Path(directory) / "snapshot.db"
                    snapshot_path.write_bytes(gzip.decompress(compressed))
                    snapshot = sqlite3.connect(snapshot_path)
                    try:
                        value = snapshot.execute("SELECT value FROM sample").fetchone()[0]
                    finally:
                        snapshot.close()
                    self.assertEqual(value, "saved")
                    return {"sha": "blob-sha"}
                if endpoint.endswith("/git/trees"):
                    return {"sha": "tree-sha"}
                if endpoint.endswith("/git/commits"):
                    return {"sha": "commit-sha"}
                return {"ref": "refs/heads/database-backup"}

            with patch.object(git_backup, "_setting", side_effect=lambda name, default="": {
                "GITHUB_BACKUP_TOKEN": "test-token",
                "GITHUB_BACKUP_REPOSITORY": "owner/repository",
                "GITHUB_BACKUP_BRANCH": "database-backup",
            }.get(name, default)), patch.object(git_backup, "_request", side_effect=request):
                result = git_backup.backup_to_github(database_engine=database_engine)

            database_engine.dispose()

        self.assertEqual(result["status"], "uploaded")
        self.assertEqual(result["commit"], "commit-sha")
        self.assertEqual(
            [call[0] for call in calls],
            ["GET", "POST", "POST", "POST", "POST"],
        )
        self.assertTrue(calls[-1][3]["ref"].endswith("database-backup"))

    def test_existing_branch_is_updated_differentially(self):
        with tempfile.TemporaryDirectory() as directory:
            database_engine = create_engine(
                f"sqlite:///{Path(directory) / 'source.db'}"
            )
            with database_engine.begin() as connection:
                connection.exec_driver_sql("CREATE TABLE sample (value TEXT)")
                connection.exec_driver_sql("INSERT INTO sample VALUES ('new')")

            calls = []

            def request(method, endpoint, token, payload=None):
                calls.append((method, endpoint, payload))
                if endpoint.endswith("/git/ref/heads/database-backup"):
                    return {"object": {"sha": "old-commit"}}
                if endpoint.endswith("/git/commits/old-commit"):
                    return {
                        "committer": {"date": "2026-11-14T10:00:00Z"},
                        "tree": {"sha": "old-tree"},
                    }
                if endpoint.endswith("/git/trees/old-tree"):
                    return {
                        "tree": [{"path": "football.db.gz", "sha": "old-blob"}]
                    }
                if endpoint.endswith("/git/blobs"):
                    return {"sha": "new-blob"}
                if endpoint.endswith("/git/trees"):
                    return {"sha": "new-tree"}
                if endpoint.endswith("/git/commits"):
                    return {"sha": "new-commit"}
                return {"ref": "updated"}

            with patch.object(
                git_backup,
                "_setting",
                side_effect=lambda name, default="": {
                    "GITHUB_BACKUP_TOKEN": "test-token",
                    "GITHUB_BACKUP_REPOSITORY": "owner/repository",
                    "GITHUB_BACKUP_BRANCH": "database-backup",
                }.get(name, default),
            ), patch.object(git_backup, "_request", side_effect=request):
                result = git_backup.backup_to_github(database_engine=database_engine)
            database_engine.dispose()

        self.assertEqual(result["status"], "uploaded")
        self.assertTrue(result["differential"])
        tree_payload = next(payload for _, endpoint, payload in calls if endpoint.endswith("/git/trees"))
        commit_payload = next(payload for _, endpoint, payload in calls if endpoint.endswith("/git/commits"))
        ref_payload = next(payload for method, endpoint, payload in calls if method == "PATCH")
        self.assertEqual(tree_payload["base_tree"], "old-tree")
        self.assertEqual(commit_payload["parents"], ["old-commit"])
        self.assertFalse(ref_payload["force"])

    def test_skips_duplicate_backup_for_utc_day(self):
        today = datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        calls = []

        def request(method, endpoint, token, payload=None):
            calls.append(endpoint)
            if endpoint.endswith("/git/ref/heads/database-backup"):
                return {"object": {"sha": "today-commit"}}
            if endpoint.endswith("/git/commits/today-commit"):
                return {
                    "committer": {"date": today},
                    "tree": {"sha": "tree-sha"},
                }
            if endpoint.endswith("/git/trees/tree-sha"):
                return {"tree": [{"path": "football.db.gz", "sha": "blob-sha"}]}
            self.fail(f"Unexpected GitHub API call: {endpoint}")

        with patch.object(
            git_backup,
            "_setting",
            side_effect=lambda name, default="": {
                "GITHUB_BACKUP_TOKEN": "test-token",
                "GITHUB_BACKUP_REPOSITORY": "owner/repository",
                "GITHUB_BACKUP_BRANCH": "database-backup",
            }.get(name, default),
        ), patch.object(git_backup, "_request", side_effect=request):
            result = git_backup.backup_to_github()

        self.assertEqual(result["status"], "skipped_daily_limit")
        self.assertEqual(len(calls), 3)

    def test_skips_when_token_is_not_configured(self):
        with patch.object(git_backup, "_setting", return_value=""):
            result = git_backup.backup_to_github()
        self.assertEqual(result, {"status": "not_configured"})


if __name__ == "__main__":
    unittest.main()
