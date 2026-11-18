"""Create a coherent SQLite snapshot and publish it to a rolling Git branch."""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import sqlite3
import tempfile
import threading
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


def _setting(name: str, default: str = "") -> str:
    value = os.getenv(name, "").strip()
    if value:
        return value
    try:
        import streamlit as st

        return str(st.secrets.get(name, default) or default).strip()
    except Exception:
        return default


def backup_is_configured() -> bool:
    return bool(_setting("GITHUB_BACKUP_TOKEN"))


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
    archive = next(
        (entry for entry in tree.get("tree", []) if entry.get("path") == "football.db.gz"),
        None,
    )
    return {
        "commit_sha": commit_sha,
        "tree_sha": tree_sha,
        "blob_sha": archive.get("sha") if archive else None,
        "committed_at": (commit.get("committer") or {}).get("date"),
    }


def latest_backup_info() -> dict:
    """Return the timestamp and commit id of the latest Git backup."""
    token = _setting("GITHUB_BACKUP_TOKEN")
    if not token:
        return {"status": "not_configured"}
    repository = _setting("GITHUB_BACKUP_REPOSITORY", _DEFAULT_REPOSITORY)
    branch = _setting("GITHUB_BACKUP_BRANCH", _DEFAULT_BRANCH)
    head = _backup_head(f"/repos/{repository}", branch, token)
    if not head:
        return {"status": "not_found", "repository": repository, "branch": branch}
    return {
        "status": "available" if head.get("blob_sha") else "missing_file",
        "repository": repository,
        "branch": branch,
        "committed_at": head.get("committed_at"),
        "commit": head.get("commit_sha"),
    }


def backup_to_github(*, database_engine=engine, force: bool = False) -> dict:
    """Publish a changed snapshot daily, retaining Git's differential history."""
    token = _setting("GITHUB_BACKUP_TOKEN")
    if not token:
        return {"status": "not_configured"}
    repository = _setting("GITHUB_BACKUP_REPOSITORY", _DEFAULT_REPOSITORY)
    branch = _setting("GITHUB_BACKUP_BRANCH", _DEFAULT_BRANCH)
    if "/" not in repository or not branch or "/" in branch:
        raise ValueError("Configuration du dépôt ou de la branche Git invalide.")

    with _backup_lock:
        root = f"/repos/{repository}"
        head = _backup_head(root, branch, token)
        today_utc = datetime.now(timezone.utc).date()
        if head and not force and head.get("committed_at"):
            committed_at = datetime.fromisoformat(
                head["committed_at"].replace("Z", "+00:00")
            )
            if committed_at.astimezone(timezone.utc).date() >= today_utc:
                return {
                    "status": "skipped_daily_limit",
                    "repository": repository,
                    "branch": branch,
                }

        # Toute sauvegarde passe par le même garde-fou : maintenance obligatoire
        # avant l'instantané, quel que soit son déclencheur (manuel, planifié ou auto).
        from services.database_maintenance import maintain_database

        maintenance = maintain_database(database_engine=database_engine)
        if maintenance.get("status") != "maintained":
            raise RuntimeError(
                "Sauvegarde annulée : maintenance SQLite non confirmée "
                f"(état : {maintenance.get('status', 'inconnu')})."
            )

        with tempfile.TemporaryDirectory(prefix="football-backup-") as temp_dir:
            archive_path = Path(temp_dir) / "football.db.gz"
            _create_database_archive(database_engine, archive_path)
            archive_bytes = archive_path.read_bytes()
            if head and head.get("blob_sha") == _git_blob_sha(archive_bytes):
                return {
                    "status": "unchanged",
                    "repository": repository,
                    "branch": branch,
                    "maintenance": maintenance,
                    "integrity_verified": True,
                    "gzip_verified": True,
                }
            content = base64.b64encode(archive_bytes).decode("ascii")
            blob = _request(
                "POST", f"{root}/git/blobs", token,
                {"content": content, "encoding": "base64"},
            )
            tree_payload = {
                "tree": [{
                    "path": "football.db.gz",
                    "mode": "100644",
                    "type": "blob",
                    "sha": blob["sha"],
                }]
            }
            if head:
                tree_payload["base_tree"] = head["tree_sha"]
            tree = _request("POST", f"{root}/git/trees", token, tree_payload)
            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            commit_payload = {
                "message": f"Sauvegarde SQLite {timestamp}",
                "tree": tree["sha"],
            }
            if head:
                commit_payload["parents"] = [head["commit_sha"]]
            commit = _request(
                "POST", f"{root}/git/commits", token, commit_payload
            )
            if head:
                _request(
                    "PATCH", f"{root}/git/refs/heads/{branch}", token,
                    {"sha": commit["sha"], "force": False},
                )
            else:
                _request(
                    "POST", f"{root}/git/refs", token,
                    {"ref": f"refs/heads/{branch}", "sha": commit["sha"]},
                )
            verified_head = _backup_head(root, branch, token)
            expected_blob_sha = _git_blob_sha(archive_bytes)
            if (
                not verified_head
                or verified_head.get("commit_sha") != commit["sha"]
                or verified_head.get("blob_sha") != expected_blob_sha
            ):
                raise RuntimeError(
                    "Sauvegarde envoyée mais vérification distante échouée : "
                    "le commit ou le fichier football.db.gz ne correspond pas."
                )
            return {
                "status": "uploaded",
                "repository": repository,
                "branch": branch,
                "commit": commit["sha"],
                "size_bytes": len(archive_bytes),
                "differential": bool(head),
                "maintenance": maintenance,
                "integrity_verified": True,
                "gzip_verified": True,
                "remote_verified": True,
            }


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
