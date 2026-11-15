"""Create a coherent SQLite snapshot and publish it to a rolling Git branch."""

from __future__ import annotations

import base64
import gzip
import json
import os
import sqlite3
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from database.database import engine

_backup_lock = threading.Lock()
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
        with snapshot.open("rb") as source_file, gzip.open(
            archive_path, "wb", compresslevel=6
        ) as archive:
            while chunk := source_file.read(1024 * 1024):
                archive.write(chunk)


def backup_to_github(*, database_engine=engine) -> dict:
    """Replace the snapshot on an orphan branch so Git history stays rolling."""
    token = _setting("GITHUB_BACKUP_TOKEN")
    if not token:
        return {"status": "not_configured"}
    repository = _setting("GITHUB_BACKUP_REPOSITORY", _DEFAULT_REPOSITORY)
    branch = _setting("GITHUB_BACKUP_BRANCH", _DEFAULT_BRANCH)
    if "/" not in repository or not branch or "/" in branch:
        raise ValueError("Configuration du dépôt ou de la branche Git invalide.")

    with _backup_lock, tempfile.TemporaryDirectory(prefix="football-backup-") as temp_dir:
        archive_path = Path(temp_dir) / "football.db.gz"
        _create_database_archive(database_engine, archive_path)
        content = base64.b64encode(archive_path.read_bytes()).decode("ascii")
        root = f"/repos/{repository}"

        blob = _request(
            "POST", f"{root}/git/blobs", token,
            {"content": content, "encoding": "base64"},
        )
        tree = _request(
            "POST", f"{root}/git/trees", token,
            {"tree": [{"path": "football.db.gz", "mode": "100644", "type": "blob", "sha": blob["sha"]}]},
        )
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        commit = _request(
            "POST", f"{root}/git/commits", token,
            {"message": f"Sauvegarde SQLite {timestamp}", "tree": tree["sha"]},
        )
        ref_endpoint = f"{root}/git/ref/heads/{branch}"
        try:
            _request("PATCH", ref_endpoint, token, {"sha": commit["sha"], "force": True})
        except RuntimeError as exc:
            if "(404)" not in str(exc):
                raise
            _request(
                "POST", f"{root}/git/refs", token,
                {"ref": f"refs/heads/{branch}", "sha": commit["sha"]},
            )
        return {
            "status": "uploaded",
            "repository": repository,
            "branch": branch,
            "commit": commit["sha"],
            "size_bytes": archive_path.stat().st_size,
        }
