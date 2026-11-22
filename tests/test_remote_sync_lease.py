import base64
import datetime
import json
import unittest
from unittest.mock import patch

from services import remote_sync_lease


class FakeGitHubContents:
    def __init__(self):
        self.records = {}
        self.next_sha = 0
        self.conflict_next_write = False

    def request(self, method, endpoint, token, payload=None):
        if endpoint.endswith("/git/ref/heads/sync-coordination"):
            return {"ref": "refs/heads/sync-coordination"}
        if "/contents/sync-locks/" not in endpoint:
            raise AssertionError(f"Unexpected endpoint: {endpoint}")
        path = endpoint.split("/contents/", 1)[1].split("?", 1)[0]
        current = self.records.get(path)
        if method == "GET":
            if current is None:
                raise RuntimeError("GitHub API (404) : file not found")
            return {
                "sha": current["sha"],
                "content": base64.b64encode(
                    json.dumps(current["record"]).encode()
                ).decode(),
                "encoding": "base64",
            }
        if method == "PUT":
            if self.conflict_next_write:
                self.conflict_next_write = False
                raise RuntimeError("GitHub API (422) : sha conflict")
            if payload.get("sha") != (current["sha"] if current else None):
                raise RuntimeError("GitHub API (409) : sha conflict")
            self.next_sha += 1
            record = json.loads(base64.b64decode(payload["content"]).decode())
            self.records[path] = {"sha": f"blob-{self.next_sha}", "record": record}
            return {"content": {"sha": self.records[path]["sha"]}}
        if method == "DELETE":
            if current is None or payload.get("sha") != current["sha"]:
                raise RuntimeError("GitHub API (409) : sha conflict")
            del self.records[path]
            return {}
        raise AssertionError(f"Unexpected method: {method}")


class RemoteSyncLeaseTests(unittest.TestCase):
    def setUp(self):
        remote_sync_lease._branch_ready.clear()
        self.github = FakeGitHubContents()
        self.request = patch.object(
            remote_sync_lease.git_backup, "_request", side_effect=self.github.request
        )
        self.request.start()
        self.repo_token = patch.object(
            remote_sync_lease, "_repo_token", return_value=("owner/repo", "token")
        )
        self.repo_token.start()
        self.branch = patch.object(remote_sync_lease, "_branch", return_value="sync-coordination")
        self.branch.start()

    def tearDown(self):
        self.branch.stop()
        self.repo_token.stop()
        self.request.stop()
        remote_sync_lease._branch_ready.clear()

    def test_claim_contention_renewal_and_owner_only_release(self):
        self.assertTrue(remote_sync_lease.claim("full-sync:exhaustive", "worker-1"))
        self.assertFalse(remote_sync_lease.claim("full-sync:exhaustive", "worker-2"))
        self.assertTrue(remote_sync_lease.renew("full-sync:exhaustive", "worker-1"))
        self.assertFalse(remote_sync_lease.release("full-sync:exhaustive", "worker-2"))
        self.assertTrue(remote_sync_lease.release("full-sync:exhaustive", "worker-1"))
        self.assertTrue(remote_sync_lease.claim("full-sync:exhaustive", "worker-2"))

    def test_expired_lease_can_be_taken_over_with_blob_sha_compare_and_swap(self):
        path = remote_sync_lease._path("full-sync:exhaustive")
        self.github.records[path] = {
            "sha": "blob-old",
            "record": {
                "resource_key": "full-sync:exhaustive",
                "owner": "stale-worker",
                "lease_until": "2000-01-01T00:00:00+00:00",
                "claimed_at": "2000-01-01T00:00:00+00:00",
                "updated_at": "2000-01-01T00:00:00+00:00",
            },
        }
        self.assertTrue(remote_sync_lease.claim("full-sync:exhaustive", "new-worker"))
        self.assertEqual(self.github.records[path]["record"]["owner"], "new-worker")

    def test_content_api_conflict_is_safe_contention(self):
        self.github.conflict_next_write = True
        self.assertFalse(remote_sync_lease.claim("full-sync:exhaustive", "worker-1"))

    def test_missing_token_fails_closed(self):
        original_repo_token = remote_sync_lease._repo_token
        with (
            patch.object(remote_sync_lease, "_repo_token", side_effect=original_repo_token),
            patch.object(remote_sync_lease.git_backup, "_backup_token", return_value=""),
        ):
            with self.assertRaises(remote_sync_lease.RemoteLeaseUnavailable):
                remote_sync_lease.claim("full-sync:exhaustive", "worker-1")

    def test_renewal_rejects_wrong_owner_and_expired_lease(self):
        self.assertTrue(remote_sync_lease.claim("full-sync:exhaustive", "worker-1"))
        self.assertFalse(remote_sync_lease.renew("full-sync:exhaustive", "worker-2"))
        path = remote_sync_lease._path("full-sync:exhaustive")
        self.github.records[path]["record"]["lease_until"] = "2000-01-01T00:00:00+00:00"
        self.assertFalse(remote_sync_lease.renew("full-sync:exhaustive", "worker-1"))


if __name__ == "__main__":
    unittest.main()
