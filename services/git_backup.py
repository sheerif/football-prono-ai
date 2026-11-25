"""Create a coherent SQLite snapshot and publish it to a rolling Git branch."""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import logging
import os
import sqlite3
import tempfile
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from database.database import engine

_backup_lock = threading.Lock()
_daily_check_lock = threading.Lock()
_daily_check_in_progress = False
_daily_last_attempt_at: datetime | None = None
_API = "https://api.github.com"
_DEFAULT_REPOSITORY = "sheerif/football-prono-ai"
_DEFAULT_BRANCH = "database-backup"
_BACKUP_LEASE_KEY = "database-backup:publish"
_BACKUP_LEASE_SECONDS = 900
_BACKUP_LEASE_HEARTBEAT_SECONDS = 60
_FULL_SYNC_LEASE_KEY = "full-sync:exhaustive"
_FULL_SYNC_LEASE_SECONDS = 900
_FULL_SYNC_LEASE_HEARTBEAT_SECONDS = 60


def _setting(name: str, default: str = "") -> str:
    value = os.getenv(name, "").strip()
    if value:
        return value
    try:
        import streamlit as st

        return str(st.secrets.get(name, default) or default).strip()
    except Exception:
        return default


def _backup_token() -> str:
    """Accept the legacy runtime name and the non-reserved secret alias."""
    return _setting("GITHUB_BACKUP_TOKEN") or _setting("BACKUP_TOKEN")


def backup_is_configured() -> bool:
    return bool(_backup_token())


def _request(method: str, endpoint: str, token: str, payload: dict | None = None):
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        f"{_API}{endpoint}",
        data=body,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            **({"Content-Type": "application/json"} if body is not None else {}),
        },
    )
    try:
        with urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read(2000).decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API ({exc.code}) : {detail}") from exc


def _create_database_archive(database_engine, archive_path: Path) -> None:
    """Use SQLite's online backup API, then gzip the consistent snapshot."""
    with tempfile.TemporaryDirectory(prefix="football-db-backup-") as temp_dir:
        snapshot = Path(temp_dir) / "football.db"
        source = database_engine.raw_connection()
        destination = sqlite3.connect(snapshot)
        try:
            source.driver_connection.backup(destination)
        finally:
            destination.close()
            source.close()
        check = sqlite3.connect(snapshot)
        try:
            integrity = check.execute("PRAGMA integrity_check").fetchone()
            if not integrity or integrity[0] != "ok":
                detail = integrity[0] if integrity else "résultat absent"
                raise RuntimeError(
                    f"Instantané SQLite invalide avant compression : {detail}"
                )
        finally:
            check.close()

        with snapshot.open("rb") as source_file, archive_path.open("wb") as raw_archive:
            with gzip.GzipFile(
                filename="", mode="wb", fileobj=raw_archive,
                compresslevel=6, mtime=0,
            ) as archive:
                while chunk := source_file.read(1024 * 1024):
                    archive.write(chunk)

    if not archive_path.is_file() or archive_path.stat().st_size == 0:
        raise RuntimeError("Archive GZIP absente ou vide.")

    # Lire tout le flux vérifie aussi le CRC et la taille GZIP avant tout envoi.
    with gzip.open(archive_path, "rb") as compressed:
        while compressed.read(1024 * 1024):
            pass


def _git_blob_sha(content: bytes) -> str:
    header = f"blob {len(content)}\0".encode("ascii")
    return hashlib.sha1(header + content).hexdigest()


def _backup_head(root: str, branch: str, token: str) -> dict | None:
    try:
        reference = _request("GET", f"{root}/git/ref/heads/{branch}", token)
    except RuntimeError as exc:
        if "(404)" in str(exc):
            return None
        raise
    commit_sha = reference["object"]["sha"]
    commit = _request("GET", f"{root}/git/commits/{commit_sha}", token)
    tree_sha = commit["tree"]["sha"]
    tree = _request("GET", f"{root}/git/trees/{tree_sha}", token)
    entries = tree.get("tree", [])
    archive = next((entry for entry in entries if entry.get("path") == "football.db.gz"), None)
    manifest = next((entry for entry in entries if entry.get("path") == "football.db.manifest.json"), None)
    parts = {
        entry["path"]: entry.get("sha")
        for entry in entries if entry.get("path", "").startswith("football.db.gz.part-")
    }
    archive_paths = [
        entry["path"] for entry in entries
        if entry.get("path") == "football.db.gz"
        or entry.get("path") == "football.db.manifest.json"
        or entry.get("path", "").startswith("football.db.gz.part-")
    ]
    return {
        "commit_sha": commit_sha,
        "tree_sha": tree_sha,
        "blob_sha": archive.get("sha") if archive else None,
        "manifest_sha": manifest.get("sha") if manifest else None,
        "part_shas": parts,
        "archive_paths": archive_paths,
        "committed_at": (commit.get("committer") or {}).get("date"),
    }


def latest_backup_info() -> dict:
    """Return the timestamp and commit id of the latest Git backup."""
    token = _backup_token()
    if not token:
        return {"status": "not_configured"}
    repository = _setting("GITHUB_BACKUP_REPOSITORY", _DEFAULT_REPOSITORY)
    branch = _setting("GITHUB_BACKUP_BRANCH", _DEFAULT_BRANCH)
    head = _backup_head(f"/repos/{repository}", branch, token)
    if not head:
        return {"status": "not_found", "repository": repository, "branch": branch}
    # Large archives are stored as a manifest plus Git blob fragments.
    # Treat that complete chunked representation as a valid backup too.
    has_legacy_archive = bool(head.get("blob_sha"))
    has_chunked_archive = bool(
        head.get("manifest_sha") and head.get("part_shas")
    )
    return {
        "status": "available" if (has_legacy_archive or has_chunked_archive) else "missing_file",
        "repository": repository,
        "branch": branch,
        "committed_at": head.get("committed_at"),
        "commit": head.get("commit_sha"),
    }


def _should_reset_backup_history(head: dict | None, now: datetime) -> bool:
    """Never force-reset a shared backup ref; rotation needs a separate branch."""
    return False


def _archive_matches_head(
    head: dict | None,
    *,
    use_chunks: bool,
    manifest_sha: str | None,
    expected_part_shas: dict[str, str],
    expected_blob_sha: str,
) -> bool:
    if not head:
        return False
    if use_chunks:
        return (
            head.get("manifest_sha") == manifest_sha
            and head.get("part_shas") == expected_part_shas
        )
    return head.get("blob_sha") == expected_blob_sha


def _is_ref_conflict(exc: RuntimeError) -> bool:
    message = str(exc).lower()
    return "(422)" in message and any(
        marker in message
        for marker in (
            "update is not a fast forward",
            "reference already exists",
            "already exists",
        )
    )


class BackupLeaseUnavailable(RuntimeError):
    """A remote publisher lease could not be acquired or maintained."""


def _remote_coordination_lease(
    resource_key: str,
    owner_prefix: str,
    *,
    lease_seconds: int,
    heartbeat_seconds: int,
):
    """Acquire, heartbeat and release one fail-closed cross-process lease."""
    # Import lazily: remote_sync_lease itself uses git_backup for GitHub requests.
    from services import remote_sync_lease

    owner = f"{owner_prefix}:{uuid.uuid4().hex}"
    try:
        claimed = remote_sync_lease.claim(
            resource_key,
            owner,
            lease_seconds=lease_seconds,
        )
    except Exception as exc:
        raise BackupLeaseUnavailable(
            f"Impossible de vérifier le verrou distant {resource_key}."
        ) from exc
    if not claimed:
        raise BackupLeaseUnavailable(
            f"Une autre instance détient le verrou distant {resource_key}."
        )

    stop = threading.Event()
    lost = threading.Event()

    def heartbeat():
        while not stop.wait(heartbeat_seconds):
            try:
                if not remote_sync_lease.renew(
                    resource_key,
                    owner,
                    lease_seconds=lease_seconds,
                ):
                    lost.set()
                    return
            except Exception:
                lost.set()
                logging.getLogger(__name__).exception(
                    "Unable to renew remote coordination lease %s", resource_key
                )
                return

    heartbeat_thread = threading.Thread(
        target=heartbeat,
        name=f"football-{owner_prefix.replace(':', '-')}-lease-heartbeat",
        daemon=True,
    )
    started = False

    def ensure_owned():
        if lost.is_set():
            raise BackupLeaseUnavailable(
                f"Le verrou distant {resource_key} a été perdu."
            )
        try:
            if not remote_sync_lease.renew(
                resource_key,
                owner,
                lease_seconds=lease_seconds,
            ):
                lost.set()
                raise BackupLeaseUnavailable(
                    f"Le verrou distant {resource_key} n'appartient plus à ce worker."
                )
        except BackupLeaseUnavailable:
            raise
        except Exception as exc:
            lost.set()
            raise BackupLeaseUnavailable(
                f"Impossible de renouveler le verrou distant {resource_key}."
            ) from exc

    try:
        heartbeat_thread.start()
        started = True
        yield ensure_owned
    finally:
        stop.set()
        if started:
            heartbeat_thread.join(timeout=heartbeat_seconds + 5)
        if started and heartbeat_thread.is_alive():
            logging.getLogger(__name__).warning(
                "Coordination lease heartbeat did not stop for %s; leaving lease to expire",
                resource_key,
            )
        elif not lost.is_set():
            try:
                remote_sync_lease.release(resource_key, owner)
            except Exception:
                logging.getLogger(__name__).exception(
                    "Unable to release remote coordination lease %s", resource_key
                )


@contextmanager
def _remote_backup_lease():
    """Serialize every backup publisher across Streamlit and GitHub Actions."""
    with _remote_coordination_lease(
        _BACKUP_LEASE_KEY,
        "database-backup",
        lease_seconds=_BACKUP_LEASE_SECONDS,
        heartbeat_seconds=_BACKUP_LEASE_HEARTBEAT_SECONDS,
    ) as ensure_owned:
        yield ensure_owned


@contextmanager
def _remote_full_sync_lease():
    """Prevent a backup from overlapping the shared exhaustive-sync writer."""
    with _remote_coordination_lease(
        _FULL_SYNC_LEASE_KEY,
        "database-backup-sync-gate",
        lease_seconds=_FULL_SYNC_LEASE_SECONDS,
        heartbeat_seconds=_FULL_SYNC_LEASE_HEARTBEAT_SECONDS,
    ) as ensure_owned:
        yield ensure_owned


def backup_to_github(*, database_engine=engine, force: bool = False) -> dict:
    """Publish a verified SQLite snapshot under a cross-process lease."""
    token = _backup_token()
    if not token:
        return {"status": "not_configured"}
    repository = _setting("GITHUB_BACKUP_REPOSITORY", _DEFAULT_REPOSITORY)
    branch = _setting("GITHUB_BACKUP_BRANCH", _DEFAULT_BRANCH)
    if "/" not in repository or not branch or "/" in branch:
        raise ValueError("Configuration du dépôt ou de la branche Git invalide.")

    # Lock order is global sync gate first, then backup publisher. Every
    # backup caller uses this order, so sync and snapshot publication cannot overlap.
    with (
        _backup_lock,
        _remote_full_sync_lease() as ensure_full_sync_lease,
        _remote_backup_lease() as ensure_backup_lease,
    ):
        def ensure_leases_owned():
            ensure_full_sync_lease()
            ensure_leases_owned()

        ensure_leases_owned()
        root = f"/repos/{repository}"
        head = _backup_head(root, branch, token)
        now_utc = datetime.now(timezone.utc)
        today_utc = now_utc.date()
        committed_at = (
            datetime.fromisoformat(head["committed_at"].replace("Z", "+00:00"))
            if head and head.get("committed_at")
            else None
        )
        reset_history = _should_reset_backup_history(head, now_utc)
        if head and not force and committed_at and not reset_history:
            if committed_at.astimezone(timezone.utc).date() >= today_utc:
                return {
                    "status": "skipped_daily_limit",
                    "repository": repository,
                    "branch": branch,
                }

        # La sauvegarde reste indépendante de la maintenance applicative.
        # L'intégrité du snapshot et du GZIP est contrôlée avant publication.

        with tempfile.TemporaryDirectory(prefix="football-backup-") as temp_dir:
            archive_path = Path(temp_dir) / "football.db.gz"
            ensure_leases_owned()
            _create_database_archive(database_engine, archive_path)
            archive_bytes = archive_path.read_bytes()
            chunk_size = 8 * 1024 * 1024
            use_chunks = len(archive_bytes) > chunk_size
            expected_part_shas = {}
            manifest_sha = None
            if use_chunks:
                parts = [
                    archive_bytes[offset:offset + chunk_size]
                    for offset in range(0, len(archive_bytes), chunk_size)
                ]
                tree_entries = []
                manifest_parts = []
                for index, part in enumerate(parts, start=1):
                    part_path = f"football.db.gz.part-{index:04d}"
                    part_sha = _git_blob_sha(part)
                    expected_part_shas[part_path] = part_sha
                    ensure_leases_owned()
                    blob = _request(
                        "POST", f"{root}/git/blobs", token,
                        {"content": base64.b64encode(part).decode("ascii"), "encoding": "base64"},
                    )
                    if blob.get("sha") != part_sha:
                        raise RuntimeError("Empreinte d'un fragment de sauvegarde incohérente.")
                    tree_entries.append({
                        "path": part_path, "mode": "100644", "type": "blob", "sha": part_sha,
                    })
                    manifest_parts.append({
                        "path": part_path, "size_bytes": len(part), "git_blob_sha": part_sha,
                    })
                manifest_bytes = json.dumps(
                    {
                        "format": 1,
                        "archive": "football.db.gz",
                        "size_bytes": len(archive_bytes),
                        "sha256": hashlib.sha256(archive_bytes).hexdigest(),
                        "parts": manifest_parts,
                    },
                    sort_keys=True, separators=(",", ":"),
                ).encode("utf-8")
                manifest_sha = _git_blob_sha(manifest_bytes)
                ensure_leases_owned()
                manifest_blob = _request(
                    "POST", f"{root}/git/blobs", token,
                    {"content": base64.b64encode(manifest_bytes).decode("ascii"), "encoding": "base64"},
                )
                if manifest_blob.get("sha") != manifest_sha:
                    raise RuntimeError("Empreinte du manifeste de sauvegarde incohérente.")
                tree_entries.append({
                    "path": "football.db.manifest.json", "mode": "100644",
                    "type": "blob", "sha": manifest_sha,
                })
                archive_tree_entries = tree_entries
                if head and _archive_matches_head(
                    head,
                    use_chunks=True,
                    manifest_sha=manifest_sha,
                    expected_part_shas=expected_part_shas,
                    expected_blob_sha="",
                ):
                    return {
                        "status": "unchanged",
                        "repository": repository,
                        "branch": branch,
                        "integrity_verified": True,
                        "gzip_verified": True,
                        "remote_verified": True,
                    }
            else:
                if head and head.get("blob_sha") == _git_blob_sha(archive_bytes):
                    return {
                        "status": "unchanged",
                        "repository": repository,
                        "branch": branch,
                        "integrity_verified": True,
                        "gzip_verified": True,
                        "remote_verified": True,
                    }
                ensure_leases_owned()
                blob = _request(
                    "POST", f"{root}/git/blobs", token,
                    {"content": base64.b64encode(archive_bytes).decode("ascii"), "encoding": "base64"},
                )
                tree_entries = [{
                    "path": "football.db.gz", "mode": "100644", "type": "blob", "sha": blob["sha"],
                }]
                archive_tree_entries = tree_entries
            expected_blob_sha = _git_blob_sha(archive_bytes)
            while True:
                current_tree_entries = [
                    dict(entry) for entry in archive_tree_entries
                ]
                if head:
                    current_paths = {
                        entry["path"] for entry in current_tree_entries
                    }
                    current_tree_entries.extend(
                        {
                            "path": path,
                            "mode": "100644",
                            "type": "blob",
                            "sha": None,
                        }
                        for path in head.get("archive_paths", [])
                        if path not in current_paths
                    )
                tree_payload = {"tree": current_tree_entries}
                if head:
                    tree_payload["base_tree"] = head["tree_sha"]
                ensure_leases_owned()
                tree = _request("POST", f"{root}/git/trees", token, tree_payload)
                reset_history = _should_reset_backup_history(head, now_utc)
                timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                commit_payload = {
                    "message": f"Sauvegarde SQLite {timestamp}",
                    "tree": tree["sha"],
                }
                if head and not reset_history:
                    commit_payload["parents"] = [head["commit_sha"]]
                ensure_leases_owned()
                commit = _request(
                    "POST", f"{root}/git/commits", token, commit_payload
                )
                try:
                    ensure_leases_owned()
                    if head:
                        _request(
                            "PATCH", f"{root}/git/refs/heads/{branch}", token,
                            {"sha": commit["sha"], "force": reset_history},
                        )
                    else:
                        _request(
                            "POST", f"{root}/git/refs", token,
                            {"ref": f"refs/heads/{branch}", "sha": commit["sha"]},
                        )
                except RuntimeError as exc:
                    if not _is_ref_conflict(exc):
                        raise
                    ensure_leases_owned()
                    refreshed_head = _backup_head(root, branch, token)
                    if _archive_matches_head(
                        refreshed_head,
                        use_chunks=use_chunks,
                        manifest_sha=manifest_sha,
                        expected_part_shas=expected_part_shas,
                        expected_blob_sha=expected_blob_sha,
                    ):
                        return {
                            "status": "unchanged",
                            "repository": repository,
                            "branch": branch,
                            "integrity_verified": True,
                            "gzip_verified": True,
                            "remote_verified": True,
                            "concurrent_update": True,
                        }
                    raise RuntimeError(
                        "Conflit concurrent sur database-backup : le snapshot préparé "
                        "avant le conflit est abandonné pour éviter d'écraser une base plus récente."
                    ) from exc

                ensure_leases_owned()
                verified_head = _backup_head(root, branch, token)
                if _archive_matches_head(
                    verified_head,
                    use_chunks=use_chunks,
                    manifest_sha=manifest_sha,
                    expected_part_shas=expected_part_shas,
                    expected_blob_sha=expected_blob_sha,
                ):
                    if verified_head.get("commit_sha") == commit["sha"]:
                        return {
                            "status": "uploaded",
                            "repository": repository,
                            "branch": branch,
                            "commit": commit["sha"],
                            "size_bytes": len(archive_bytes),
                            "differential": bool(head),
                            "history_reset": reset_history,
                            "integrity_verified": True,
                            "gzip_verified": True,
                            "remote_verified": True,
                        }
                    return {
                        "status": "unchanged",
                        "repository": repository,
                        "branch": branch,
                        "integrity_verified": True,
                        "gzip_verified": True,
                        "remote_verified": True,
                        "concurrent_update": True,
                    }
                raise RuntimeError(
                    "Sauvegarde envoyée mais vérification distante échouée : "
                    "la branche a changé pendant la publication; le snapshot local n'est pas republié."
                )


def start_daily_backup_check() -> bool:
    """Start a non-blocking daily check, retrying failures after one hour."""
    global _daily_check_in_progress, _daily_last_attempt_at
    if not backup_is_configured():
        return False
    now = datetime.now(timezone.utc)
    with _daily_check_lock:
        if _daily_check_in_progress:
            return False
        if (
            _daily_last_attempt_at
            and now - _daily_last_attempt_at < timedelta(hours=1)
        ):
            return False
        _daily_check_in_progress = True

    def run():
        global _daily_check_in_progress, _daily_last_attempt_at
        try:
            backup_to_github()
        except Exception:
            import logging

            logging.exception("Échec de la sauvegarde quotidienne SQLite vers GitHub")
        finally:
            with _daily_check_lock:
                _daily_last_attempt_at = datetime.now(timezone.utc)
                _daily_check_in_progress = False

    threading.Thread(
        target=run,
        name="football-prono-daily-backup-check",
        daemon=True,
    ).start()
    return True
