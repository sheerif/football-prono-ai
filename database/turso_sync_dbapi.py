"""Adaptateur DB-API local-first pour une réplique Turso synchronisée.

Toutes les lectures SQL sont exécutées dans le fichier local. Les écritures
sont validées localement, puis poussées vers Turso après le commit. Un pull
incrémental et temporisé récupère les écritures faites par un autre processus
(notamment la synchronisation GitHub Actions).
"""

from __future__ import annotations

import datetime
import logging
import math
import os
import re
import sqlite3
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import turso
import turso.sync


logger = logging.getLogger(__name__)

apilevel = turso.apilevel
threadsafety = turso.threadsafety
paramstyle = turso.paramstyle
sqlite_version_info = turso.sqlite_version_info
sqlite_version = turso.sqlite_version

Warning = turso.Warning
Error = turso.Error
InterfaceError = turso.InterfaceError
DatabaseError = turso.DatabaseError
DataError = turso.DataError
OperationalError = turso.OperationalError
IntegrityError = turso.IntegrityError
InternalError = turso.InternalError
ProgrammingError = turso.ProgrammingError
NotSupportedError = turso.NotSupportedError
Binary = sqlite3.Binary


def _utc_now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def _normalize_value(value: Any) -> Any:
    module_name = type(value).__module__.split(".", 1)[0]
    if module_name == "numpy" and hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _normalize_parameters(parameters: Any) -> Any:
    if parameters is None:
        return None
    if isinstance(parameters, Mapping):
        return {key: _normalize_value(value) for key, value in parameters.items()}
    return tuple(_normalize_value(value) for value in parameters)


def _changes_data(operation: str) -> bool:
    statement = str(operation or "").lstrip()
    while statement.startswith("--"):
        statement = statement.partition("\n")[2].lstrip()
    keyword = statement.partition(" ")[0].partition("\n")[0].upper()
    if keyword == "WITH":
        return bool(re.search(r"\b(INSERT|UPDATE|DELETE|REPLACE)\b", statement, re.I))
    return keyword not in {"", "SELECT", "EXPLAIN", "PRAGMA"}


@dataclass
class _ReplicaState:
    path: str
    lock: threading.RLock = field(default_factory=threading.RLock)
    initialized: bool = False
    pending_push: bool = False
    last_pull_monotonic: float = 0.0
    last_push_attempt_monotonic: float = 0.0
    last_pull_at: str | None = None
    last_push_at: str | None = None
    last_error: str | None = None


_states: dict[str, _ReplicaState] = {}
_states_lock = threading.Lock()


def _state_for(path: str) -> _ReplicaState:
    absolute_path = os.path.abspath(path)
    with _states_lock:
        return _states.setdefault(absolute_path, _ReplicaState(absolute_path))


class Cursor:
    def __init__(self, connection: "Connection", raw_cursor):
        self.connection = connection
        self._raw_cursor = raw_cursor

    def __getattr__(self, name: str):
        return getattr(self._raw_cursor, name)

    def execute(self, operation: str, parameters: Any = None):
        normalized = _normalize_parameters(parameters)
        if normalized is None:
            self._raw_cursor.execute(operation)
        else:
            self._raw_cursor.execute(operation, normalized)
        if _changes_data(operation):
            self.connection._dirty = True
        return self

    def executemany(self, operation: str, seq_of_parameters: Iterable[Any]):
        normalized = [_normalize_parameters(params) for params in seq_of_parameters]
        self._raw_cursor.executemany(operation, normalized)
        if normalized and _changes_data(operation):
            self.connection._dirty = True
        return self

    def executescript(self, script: str):
        self._raw_cursor.executescript(script)
        if str(script).strip():
            self.connection._dirty = True
        return self


class Connection:
    def __init__(
        self,
        raw_connection,
        state: _ReplicaState,
        *,
        pull_interval_seconds: int,
        push_retry_seconds: int,
        strict_push: bool,
    ):
        object.__setattr__(self, "_raw_connection", raw_connection)
        object.__setattr__(self, "_state", state)
        object.__setattr__(self, "_dirty", False)
        object.__setattr__(self, "_pull_interval_seconds", pull_interval_seconds)
        object.__setattr__(self, "_push_retry_seconds", push_retry_seconds)
        object.__setattr__(self, "_strict_push", strict_push)
        object.__setattr__(self, "_closed", False)

    def __getattr__(self, name: str):
        return getattr(self._raw_connection, name)

    def __setattr__(self, name: str, value: Any):
        if name.startswith("_"):
            object.__setattr__(self, name, value)
        else:
            setattr(self._raw_connection, name, value)

    def cursor(self):
        return Cursor(self, self._raw_connection.cursor())

    def execute(self, operation: str, parameters: Any = None):
        return self.cursor().execute(operation, parameters)

    def executemany(self, operation: str, parameters: Iterable[Any]):
        return self.cursor().executemany(operation, parameters)

    def commit(self):
        self._raw_connection.commit()
        if self._dirty:
            self._state.pending_push = True
            self._dirty = False
        if self._state.pending_push:
            pushed = self._push_pending(force=True)
            if not pushed and self._strict_push:
                raise OperationalError(
                    "Écriture conservée localement, mais sauvegarde Turso en attente."
                )

    def rollback(self):
        self._raw_connection.rollback()
        self._dirty = False

    def close(self):
        if self._closed:
            return
        try:
            if self._state.pending_push:
                self._push_pending(force=False)
        finally:
            self._raw_connection.close()
            self._closed = True

    def create_function(self, *_args, **_kwargs):
        # Requis par le dialecte SQLite de SQLAlchemy. Les fonctions REGEXP et
        # FLOOR ajoutées par défaut ne sont pas utilisées par l'application.
        return None

    def pull_if_due(self, *, force: bool = False) -> bool:
        now = time.monotonic()
        state = self._state
        with state.lock:
            if state.pending_push and not self._push_pending(force=False):
                return False
            if not force and now - state.last_pull_monotonic < self._pull_interval_seconds:
                return False
            try:
                self._raw_connection.pull()
            except Exception as exc:
                state.last_error = f"pull: {exc}"
                logger.warning("Synchronisation descendante Turso différée : %s", exc)
                return False
            state.last_pull_monotonic = time.monotonic()
            state.last_pull_at = _utc_now()
            state.last_error = None
            return True

    def _push_pending(self, *, force: bool) -> bool:
        state = self._state
        now = time.monotonic()
        with state.lock:
            if not state.pending_push:
                return True
            if (
                not force
                and now - state.last_push_attempt_monotonic < self._push_retry_seconds
            ):
                return False
            state.last_push_attempt_monotonic = now
            try:
                self._raw_connection.push()
            except Exception as exc:
                state.last_error = f"push: {exc}"
                logger.warning(
                    "Écriture locale conservée ; sauvegarde Turso différée : %s", exc
                )
                return False
            state.pending_push = False
            state.last_push_at = _utc_now()
            state.last_error = None
            return True


def connect(
    path: str,
    url: str,
    auth_token: str,
    *,
    pull_interval_seconds: int = 3600,
    push_retry_seconds: int = 300,
    strict_push: bool = False,
    **_kwargs,
):
    local_path = os.path.abspath(path)
    Path(local_path).parent.mkdir(parents=True, exist_ok=True)
    state = _state_for(local_path)
    existed = os.path.exists(local_path) and os.path.getsize(local_path) > 0
    with state.lock:
        raw_connection = turso.sync.connect(
            local_path,
            remote_url=url,
            auth_token=auth_token,
            bootstrap_if_empty=True,
        )
        connection = Connection(
            raw_connection,
            state,
            pull_interval_seconds=max(60, int(pull_interval_seconds)),
            push_retry_seconds=max(60, int(push_retry_seconds)),
            strict_push=bool(strict_push),
        )
        if not state.initialized:
            state.initialized = True
            if existed:
                connection.pull_if_due(force=True)
            else:
                state.last_pull_monotonic = time.monotonic()
                state.last_pull_at = _utc_now()
                state.last_error = None
        return connection


def replica_status(path: str) -> dict[str, Any]:
    state = _state_for(path)
    with state.lock:
        return {
            "path": state.path,
            "initialized": state.initialized,
            "pending_push": state.pending_push,
            "last_pull_at": state.last_pull_at,
            "last_push_at": state.last_push_at,
            "last_error": state.last_error,
        }


def _reset_states_for_tests() -> None:
    with _states_lock:
        _states.clear()
