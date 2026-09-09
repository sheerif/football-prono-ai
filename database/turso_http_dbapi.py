"""Petit adaptateur DB-API pour Turso via HTTPS, sans extension native.

Turso valide chaque instruction HTTP séparément. Les synchronisations de
l'application sont déjà incrémentales et idempotentes ; ``commit`` et
``rollback`` sont donc présents pour le contrat DB-API mais n'ouvrent pas de
transaction longue côté serveur.
"""

from __future__ import annotations

import sqlite3
import math
from collections.abc import Mapping
from typing import Any, Iterable

import libsql_client


apilevel = "2.0"
threadsafety = 2
paramstyle = "qmark"
sqlite_version_info = sqlite3.sqlite_version_info
sqlite_version = sqlite3.sqlite_version

Warning = sqlite3.Warning
Error = sqlite3.Error
InterfaceError = sqlite3.InterfaceError
DatabaseError = sqlite3.DatabaseError
DataError = sqlite3.DataError
OperationalError = sqlite3.OperationalError
IntegrityError = sqlite3.IntegrityError
InternalError = sqlite3.InternalError
ProgrammingError = sqlite3.ProgrammingError
NotSupportedError = sqlite3.NotSupportedError
Binary = sqlite3.Binary


def _normalize_value(value: Any) -> Any:
    """Convertit les scalaires pandas/numpy en valeurs acceptées par libSQL."""
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


class Cursor:
    def __init__(self, connection: "Connection"):
        self.connection = connection
        self.arraysize = 1
        self.description = None
        self.rowcount = -1
        self.lastrowid = None
        self._rows: list[tuple] = []
        self._offset = 0
        self._closed = False

    def execute(self, operation: str, parameters: Any = None):
        self._ensure_open()
        try:
            result = self.connection._client.execute(
                operation,
                _normalize_parameters(parameters),
            )
        except Exception as exc:
            raise OperationalError(str(exc)) from exc
        self._load_result(result)
        return self

    def executemany(self, operation: str, seq_of_parameters: Iterable[Any]):
        self._ensure_open()
        statements = [
            (operation, _normalize_parameters(parameters))
            for parameters in seq_of_parameters
        ]
        if not statements:
            self.rowcount = 0
            return self
        try:
            results = self.connection._client.batch(statements)
        except Exception as exc:
            raise OperationalError(str(exc)) from exc
        self.rowcount = sum(int(result.rows_affected or 0) for result in results)
        self.lastrowid = results[-1].last_insert_rowid
        self.description = None
        self._rows = []
        self._offset = 0
        return self

    def fetchone(self):
        self._ensure_open()
        if self._offset >= len(self._rows):
            return None
        row = self._rows[self._offset]
        self._offset += 1
        return row

    def fetchmany(self, size: int | None = None):
        self._ensure_open()
        amount = self.arraysize if size is None else max(0, int(size))
        rows = self._rows[self._offset : self._offset + amount]
        self._offset += len(rows)
        return rows

    def fetchall(self):
        self._ensure_open()
        rows = self._rows[self._offset :]
        self._offset = len(self._rows)
        return rows

    def close(self):
        self._closed = True
        self._rows = []

    def setinputsizes(self, *_args, **_kwargs):
        return None

    def setoutputsize(self, *_args, **_kwargs):
        return None

    def _load_result(self, result) -> None:
        columns = tuple(result.columns or ())
        self.description = (
            tuple((name, None, None, None, None, None, None) for name in columns)
            if columns
            else None
        )
        self._rows = [tuple(row) for row in result.rows]
        self._offset = 0
        self.rowcount = int(result.rows_affected or 0) if not columns else -1
        self.lastrowid = result.last_insert_rowid

    def _ensure_open(self) -> None:
        if self._closed:
            raise ProgrammingError("Cursor fermé")


class Connection:
    def __init__(self, url: str, auth_token: str, timeout: float = 15.0):
        self._client = libsql_client.create_client_sync(
            url,
            auth_token=auth_token,
        )
        self._closed = False
        self.isolation_level = None
        self.timeout = timeout

    def cursor(self):
        self._ensure_open()
        return Cursor(self)

    def execute(self, operation: str, parameters: Any = None):
        return self.cursor().execute(operation, parameters)

    def executemany(self, operation: str, parameters: Iterable[Any]):
        return self.cursor().executemany(operation, parameters)

    def commit(self):
        self._ensure_open()

    def rollback(self):
        self._ensure_open()

    def close(self):
        if not self._closed:
            self._client.close()
            self._closed = True

    def create_function(self, *_args, **_kwargs):
        # Les fonctions SQL personnalisées de la dialecte SQLite ne sont pas
        # requises par les requêtes de l'application.
        return None

    @property
    def in_transaction(self) -> bool:
        return False

    def _ensure_open(self) -> None:
        if self._closed:
            raise ProgrammingError("Connexion fermée")


def connect(url: str, auth_token: str, timeout: float = 15.0, **_kwargs):
    http_url = "https://" + url.removeprefix("sqlite+libsql://").removeprefix(
        "libsql://"
    ).split("?", 1)[0]
    return Connection(http_url, auth_token, timeout=timeout)
