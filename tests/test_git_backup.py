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
    class ImmediateThread:
        def __init__(self, target, **_kwargs):
            self.target = target

        def start(self):
            self.target()

    def test_uploads_a_consistent_compressed_sqlite_copy_to_rolling_branch(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "source.db"
            database_engine = create_engine(f"sqlite:///{database_path}")
            with database_engine.begin() as connection:
                connection.exec_driver_sql("CREATE TABLE sample (value TEXT)")
                connection.exec_driver_sql("INSERT INTO sample VALUES ('saved')")

            calls = []
            uploaded = {}

            def request(method, endpoint, token, payload=None):
                calls.append((method, endpoint, token, payload))
                if endpoint.endswith("/git/blobs"):
                    compressed = base64.b64decode(payload["content"])
                    uploaded["blob_sha"] = git_backup._git_blob_sha(compressed)
                    snapshot_path = Path(directory) / "snapshot.db"
                    snapshot_path.write_bytes(gzip.decompress(compressed))
                    snapshot = sqlite3.connect(snapshot_path)
                    try:
                        value = snapshot.execute("SELECT value FROM sample").fetchone()[0]
                        integrity = snapshot.execute("PRAGMA integrity_check").fetchone()[0]
                    finally:
                        snapshot.close()
                    self.assertEqual(value, "saved")
                    self.assertEqual(integrity, "ok")
                    return {"sha": uploaded["blob_sha"]}
                if endpoint.endswith("/git/trees"):
                    return {"sha": "tree-sha"}
                if endpoint.endswith("/git/commits"):
                    return {"sha": "commit-sha"}
                return {"ref": "refs/heads/database-backup"}

            def backup_head(*_args):
                if not uploaded:
                    return None
                return {
                    "commit_sha": "commit-sha",
                    "blob_sha": uploaded["blob_sha"],
                }

            with (
                patch.object(git_backup, "_setting", side_effect=lambda name, default="": {
                    "GITHUB_BACKUP_TOKEN": "test-token",
                    "GITHUB_BACKUP_REPOSITORY": "owner/repository",
                    "GITHUB_BACKUP_BRANCH": "database-backup",
                }.get(name, default)),
                patch.object(git_backup, "_request", side_effect=request),
                patch.object(git_backup, "_backup_head", side_effect=backup_head),
                patch("services.database_maintenance.maintain_database") as maintenance,
            ):
                result = git_backup.backup_to_github(database_engine=database_engine)

            database_engine.dispose()

        self.assertEqual(result["status"], "uploaded")
        self.assertEqual(result["commit"], "commit-sha")
        self.assertTrue(result["integrity_verified"])
        self.assertTrue(result["gzip_verified"])
        self.assertTrue(result["remote_verified"])
        maintenance.assert_not_called()
        self.assertEqual(
            [call[0] for call in calls],
            ["POST", "POST", "POST", "POST"],
        )
        self.assertTrue(calls[-1][3]["ref"].endswith("database-backup"))

    def test_existing_branch_is_updated_differentially_and_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            database_engine = create_engine(
                f"sqlite:///{Path(directory) / 'source.db'}"
            )
            with database_engine.begin() as connection:
                connection.exec_driver_sql("CREATE TABLE sample (value TEXT)")
                connection.exec_driver_sql("INSERT INTO sample VALUES ('new')")

            calls = []
            uploaded = {}

            def request(method, endpoint, token, payload=None):
                calls.append((method, endpoint, payload))
                if endpoint.endswith("/git/blobs"):
                    compressed = base64.b64decode(payload["content"])
                    uploaded["blob_sha"] = git_backup._git_blob_sha(compressed)
                    return {"sha": uploaded["blob_sha"]}
                if endpoint.endswith("/git/trees"):
                    return {"sha": "new-tree"}
                if endpoint.endswith("/git/commits"):
                    return {"sha": "new-commit"}
                return {"ref": "updated"}

            old_head = {
                "commit_sha": "old-commit",
                "tree_sha": "old-tree",
                "blob_sha": "old-blob",
                "committed_at": "2026-11-14T10:00:00Z",
            }

            def backup_head(*_args):
                if not uploaded:
                    return old_head
                return {
                    "commit_sha": "new-commit",
                    "blob_sha": uploaded["blob_sha"],
                }

            with (
                patch.object(
                    git_backup,
                    "_setting",
                    side_effect=lambda name, default="": {
                        "GITHUB_BACKUP_TOKEN": "test-token",
                        "GITHUB_BACKUP_REPOSITORY": "owner/repository",
                        "GITHUB_BACKUP_BRANCH": "database-backup",
                    }.get(name, default),
                ),
                patch.object(git_backup, "_request", side_effect=request),
                patch.object(git_backup, "_backup_head", side_effect=backup_head),
            ):
                result = git_backup.backup_to_github(database_engine=database_engine)
            database_engine.dispose()

        self.assertEqual(result["status"], "uploaded")
        self.assertTrue(result["differential"])
        self.assertTrue(result["remote_verified"])
        tree_payload = next(payload for _, endpoint, payload in calls if endpoint.endswith("/git/trees"))
        commit_payload = next(payload for _, endpoint, payload in calls if endpoint.endswith("/git/commits"))
        ref_payload = next(payload for method, endpoint, payload in calls if method == "PATCH")
        self.assertEqual(tree_payload["base_tree"], "old-tree")
        self.assertEqual(commit_payload["parents"], ["old-commit"])
        self.assertFalse(ref_payload["force"])
        self.assertFalse(result["history_reset"])

    def test_backup_history_resets_at_utc_month_boundary(self):
        now = datetime(2026, 11, 20, 0, 0, tzinfo=timezone.utc)
        same_month = {"committed_at": "2026-11-01T00:00:00Z"}
        previous_month = {"committed_at": "2026-10-31T23:59:00Z"}
        self.assertFalse(git_backup._should_reset_backup_history(same_month, now))
        self.assertTrue(git_backup._should_reset_backup_history(previous_month, now))
        self.assertFalse(git_backup._should_reset_backup_history(None, now))

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

    def test_accepts_non_reserved_backup_token_secret_alias(self):
        with patch.object(
            git_backup,
            "_setting",
            side_effect=lambda name, default="": {
                "BACKUP_TOKEN": "alias-token",
            }.get(name, default),
        ):
            self.assertEqual(git_backup._backup_token(), "alias-token")
            self.assertTrue(git_backup.backup_is_configured())

    def test_daily_scheduler_starts_a_non_blocking_backup_check(self):
        with (
            patch.object(git_backup, "backup_is_configured", return_value=True),
            patch.object(git_backup, "_daily_check_in_progress", False),
            patch.object(git_backup, "_daily_last_attempt_at", None),
            patch.object(git_backup.threading, "Thread", self.ImmediateThread),
            patch.object(
                git_backup, "backup_to_github", return_value={"status": "uploaded"}
            ) as backup,
        ):
            started = git_backup.start_daily_backup_check()

        self.assertTrue(started)
        backup.assert_called_once_with()

    def test_latest_backup_info_exposes_github_commit_timestamp(self):
        expected = {
            "status": "available",
            "repository": "owner/repository",
            "branch": "database-backup",
            "committed_at": "2026-11-15T19:41:45Z",
            "commit": "commit-sha",
        }
        with patch.object(
            git_backup,
            "_setting",
            side_effect=lambda name, default="": {
                "GITHUB_BACKUP_TOKEN": "test-token",
                "GITHUB_BACKUP_REPOSITORY": "owner/repository",
                "GITHUB_BACKUP_BRANCH": "database-backup",
            }.get(name, default),
        ), patch.object(
            git_backup,
            "_backup_head",
            return_value={
                "blob_sha": "blob-sha",
                "committed_at": expected["committed_at"],
                "commit_sha": expected["commit"],
            },
        ):
            result = git_backup.latest_backup_info()

        self.assertEqual(result, expected)


    def test_latest_backup_info_recognizes_chunked_archive(self):
        expected = {
            "status": "available",
            "repository": "owner/repository",
            "branch": "database-backup",
            "committed_at": "2026-11-18T20:45:00Z",
            "commit": "chunked-commit",
        }
        with patch.object(
            git_backup,
            "_setting",
            side_effect=lambda name, default="": {
                "GITHUB_BACKUP_TOKEN": "test-token",
                "GITHUB_BACKUP_REPOSITORY": "owner/repository",
                "GITHUB_BACKUP_BRANCH": "database-backup",
            }.get(name, default),
        ), patch.object(
            git_backup,
            "_backup_head",
            return_value={
                "blob_sha": None,
                "manifest_sha": "manifest-sha",
                "part_shas": {
                    "football.db.gz.part-0001": "part-sha-1",
                    "football.db.gz.part-0002": "part-sha-2",
                },
                "committed_at": expected["committed_at"],
                "commit_sha": expected["commit"],
            },
        ):
            result = git_backup.latest_backup_info()

        self.assertEqual(result, expected)



if __name__ == "__main__":
    unittest.main()
