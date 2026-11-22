"""Remote coordination leases shared by Streamlit and GitHub Actions.

SQLite download claims remain useful within one database, but Cloud and Actions
use separate local SQLite files. This module arbitrates exhaustive sync through
a compare-and-swap GitHub Contents update on a dedicated coordination branch.
It deliberately fails closed when remote coordination cannot be verified.
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import json
import threading
from urllib.parse import quote

from services import git_backup

DEFAULT_BRANCH = "sync-coordination"
LEASE_PATH_PREFIX = "sync-locks"
LEASE_SECONDS = 900
_branch_lock = threading.Lock()
_branch_ready: set[tuple[str, str]] = set()


class RemoteLeaseUnavailable(RuntimeError):
    """Remote coordination cannot be verified; exhaustive sync must not run."""


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _repo_token() -> tuple[str, str]:
    token = git_backup._backup_token()
    repository = git_backup._setting(
        "GITHUB_BACKUP_REPOSITORY", git_backup._DEFAULT_REPOSITORY
    )
    if not token:
        raise RemoteLeaseUnavailable(
            "Token GitHub absent : verrou distant de synchronisation indisponible."
        )
    if not repository or "/" not in repository:
        raise RemoteLeaseUnavailable("Repository GitHub de coordination invalide.")
    return repository, token


def _branch() -> str:
    return git_backup._setting(
        "GITHUB_SYNC_COORDINATION_BRANCH", DEFAULT_BRANCH
    )


def _is_http_error(exc: Exception, code: int) -> bool:
    return f"({code})" in str(exc)


def _ensure_branch(repository: str, branch: str, token: str) -> None:
    identity = (repository, branch)
    with _branch_lock:
        if identity in _branch_ready:
            return
        root = f"/repos/{repository}"
        try:
            git_backup._request(
                "GET", f"{root}/git/ref/heads/{quote(branch, safe='')}", token
            )
            _branch_ready.add(identity)
            return
        except RuntimeError as exc:
            if not _is_http_error(exc, 404):
                raise

        # GitHub rejects an empty tree ("Invalid tree info"). Create a
        # minimal marker blob/tree, then an orphan commit with no parents so
        # this branch still does not inherit the application's backup history.
        marker = b"Remote synchronization lease coordination branch.\\n"
        marker_blob = git_backup._request(
            "POST",
            f"{root}/git/blobs",
            token,
            {
                "content": base64.b64encode(marker).decode("ascii"),
                "encoding": "base64",
            },
        )
        coordination_tree = git_backup._request(
            "POST",
            f"{root}/git/trees",
            token,
            {
                "tree": [
                    {
                        "path": ".coordination-root",
                        "mode": "100644",
                        "type": "blob",
                        "sha": marker_blob["sha"],
                    }
                ]
            },
        )
        empty_commit = git_backup._request(
            "POST",
            f"{root}/git/commits",
            token,
            {
                "message": "Initialize remote sync coordination branch",
                "tree": coordination_tree["sha"],
                "parents": [],
            },
        )
        try:
            git_backup._request(
                "POST",
                f"{root}/git/refs",
                token,
                {"ref": f"refs/heads/{branch}", "sha": empty_commit["sha"]},
            )
        except RuntimeError as exc:
            # A competing initializer may have created the branch after GET.
            if not _is_http_error(exc, 422):
                raise
            git_backup._request(
                "GET", f"{root}/git/ref/heads/{quote(branch, safe='')}", token
            )
        _branch_ready.add(identity)


def _path(resource_key: str) -> str:
    digest = hashlib.sha256(str(resource_key).encode("utf-8")).hexdigest()
    return f"{LEASE_PATH_PREFIX}/{digest}.json"


def _read_lock(repository: str, branch: str, path: str, token: str):
    root = f"/repos/{repository}"
    endpoint = f"{root}/contents/{quote(path, safe='/')}?ref={quote(branch, safe='')}"
    try:
        response = git_backup._request("GET", endpoint, token)
    except RuntimeError as exc:
        if _is_http_error(exc, 404):
            return None, None
        raise
    encoded = response.get("content") or ""
    try:
        raw = base64.b64decode(encoded).decode("utf-8")
        record = json.loads(raw)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RemoteLeaseUnavailable("Fichier de lease distant illisible.") from exc
    return record, response.get("sha")


def _write_lock(
    repository: str,
    branch: str,
    path: str,
    token: str,
    record: dict,
    sha: str | None,
) -> bool:
    root = f"/repos/{repository}"
    payload = {
        "message": "Update remote full-sync lease",
        "content": base64.b64encode(
            (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()
        ).decode("ascii"),
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha
    try:
        git_backup._request(
            "PUT", f"{root}/contents/{quote(path, safe='/')}", token, payload
        )
        return True
    except RuntimeError as exc:
        # Contents API writes are CAS using the existing blob SHA. Conflicts
        # mean another contender changed the lease; never overwrite it.
        if _is_http_error(exc, 409) or _is_http_error(exc, 422):
            return False
        raise


def claim(
    resource_key: str,
    owner: str,
    *,
    lease_seconds: int = LEASE_SECONDS,
) -> bool:
    """Acquire or take over an expired lease with a compare-and-swap write."""
    repository, token = _repo_token()
    branch = _branch()
    _ensure_branch(repository, branch, token)
    path = _path(resource_key)
    now = _now()
    existing, sha = _read_lock(repository, branch, path, token)
    if existing:
        if existing.get("owner") == str(owner):
            pass
        else:
            try:
                expiry = datetime.datetime.fromisoformat(
                    str(existing["lease_until"])
                )
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=datetime.UTC)
                if expiry > now:
                    return False
            except (KeyError, TypeError, ValueError):
                # Corrupt or incomplete leases are treated as expired but
                # overwritten only through the SHA compare-and-swap.
                pass
    record = {
        "resource_key": str(resource_key),
        "owner": str(owner),
        "claimed_at": now.isoformat(),
        "lease_until": (now + datetime.timedelta(seconds=max(1, int(lease_seconds)))).isoformat(),
        "updated_at": now.isoformat(),
    }
    return _write_lock(repository, branch, path, token, record, sha)


def renew(
    resource_key: str,
    owner: str,
    *,
    lease_seconds: int = LEASE_SECONDS,
) -> bool:
    """Renew only an unexpired lease still owned by the caller."""
    repository, token = _repo_token()
    branch = _branch()
    _ensure_branch(repository, branch, token)
    path = _path(resource_key)
    now = _now()
    existing, sha = _read_lock(repository, branch, path, token)
    if not existing or not sha or existing.get("owner") != str(owner):
        return False
    try:
        expiry = datetime.datetime.fromisoformat(str(existing["lease_until"]))
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=datetime.UTC)
    except (KeyError, TypeError, ValueError):
        return False
    if expiry <= now:
        return False
    record = dict(existing)
    record["lease_until"] = (
        now + datetime.timedelta(seconds=max(1, int(lease_seconds)))
    ).isoformat()
    record["updated_at"] = now.isoformat()
    return _write_lock(repository, branch, path, token, record, sha)


def release(resource_key: str, owner: str) -> bool:
    """Delete the lease only if the remote record still belongs to this owner."""
    repository, token = _repo_token()
    branch = _branch()
    _ensure_branch(repository, branch, token)
    path = _path(resource_key)
    existing, sha = _read_lock(repository, branch, path, token)
    if not existing or not sha or existing.get("owner") != str(owner):
        return False
    root = f"/repos/{repository}"
    payload = {
        "message": "Release remote full-sync lease",
        "sha": sha,
        "branch": branch,
    }
    try:
        git_backup._request(
            "DELETE", f"{root}/contents/{quote(path, safe='/')}", token, payload
        )
        return True
    except RuntimeError as exc:
        if _is_http_error(exc, 409) or _is_http_error(exc, 422):
            return False
        raise
