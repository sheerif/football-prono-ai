"""Moteur central de contrôle de fiabilité des données.

Ce service est indépendant de Streamlit et expose un instantané explicable.
Une mesure inconnue reste inconnue : les erreurs de lecture ne deviennent
jamais des compteurs à zéro.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json

from sqlalchemy import inspect, text

from database.database import engine


CORE_TABLES = ("leagues", "teams", "matches", "standings")
ARCHIVE_SAMPLE_SIZE = 100


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _parse_timestamp(value) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _age_seconds(value, now: dt.datetime) -> float | None:
    parsed = _parse_timestamp(value)
    if parsed is None:
        return None
    return max(0.0, (now - parsed).total_seconds())


def _freshness_status(age_seconds: float | None, warning_hours: int, critical_hours: int) -> str:
    if age_seconds is None:
        return "unknown"
    if age_seconds >= critical_hours * 3600:
        return "critical"
    if age_seconds >= warning_hours * 3600:
        return "warning"
    return "healthy"


def _table_columns(inspector, table_name: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table_name)}


def _latest_value(connection, table_name: str, columns: set[str], candidates: tuple[str, ...]):
    column = next((name for name in candidates if name in columns), None)
    if column is None:
        return None
    # Table and column names originate from fixed internal allowlists/introspection.
    return connection.execute(
        text(f'SELECT MAX("{column}") FROM "{table_name}"')
    ).scalar()


def _core_sync_freshness(connection, inspector) -> dict | None:
    """Return the last successful core-season sync, not a fabricated table timestamp.

    The current importer updates leagues, teams, fixtures and standings as one
    league/season operation. Its registry is therefore the authoritative
    shared freshness signal when individual tables have no timestamp column.
    """
    if not inspector.has_table("resource_sync_state"):
        return None
    rows = connection.execute(text(
        "SELECT status, COUNT(*) AS total FROM resource_sync_state "
        "WHERE resource_type = 'core_season' GROUP BY status"
    )).mappings().all()
    counts = {str(row["status"]): int(row["total"]) for row in rows}
    latest = connection.execute(text(
        "SELECT MAX(updated_at) FROM resource_sync_state "
        "WHERE resource_type = 'core_season' AND status = 'complete'"
    )).scalar()
    return {"last_updated_at": str(latest) if latest is not None else None,
            "status_counts": counts}


def _latest_api_retrieval(connection, inspector, table_name: str):
    """Find the latest successful request-state timestamp for this table endpoint."""
    if not inspector.has_table("api_request_state"):
        return None
    columns = _table_columns(inspector, "api_request_state")
    if not {"endpoint", "retrieved_at", "http_status"}.issubset(columns):
        return None
    endpoints = {
        "leagues": ("/leagues",),
        "teams": ("/teams",),
        "matches": ("/fixtures",),
        "standings": ("/standings",),
    }.get(table_name, ())
    if not endpoints:
        return None
    placeholders = ", ".join(f":endpoint_{index}" for index in range(len(endpoints)))
    params = {f"endpoint_{index}": endpoint for index, endpoint in enumerate(endpoints)}
    return connection.execute(text(
        "SELECT MAX(retrieved_at) FROM api_request_state "
        f"WHERE endpoint IN ({placeholders}) AND http_status >= 200 AND http_status < 300"
    ), params).scalar()


def _table_check(
    connection, inspector, table_name: str, now: dt.datetime,
    core_sync: dict | None = None,
) -> dict:
    if not inspector.has_table(table_name):
        return {
            "name": table_name, "status": "unknown", "count": None,
            "last_updated_at": None, "age_seconds": None,
            "message": "Table absente : mesure indisponible.",
        }
    columns = _table_columns(inspector, table_name)
    try:
        count = int(connection.execute(text(f'SELECT COUNT(*) FROM "{table_name}"')).scalar_one())
        last_updated = _latest_value(
            connection, table_name, columns,
            ("updated_at", "retrieved_at", "finished_at", "created_at"),
        )
        timestamp_source = "table"
        if last_updated is None:
            api_retrieved_at = _latest_api_retrieval(connection, inspector, table_name)
            if api_retrieved_at is not None:
                last_updated = api_retrieved_at
                timestamp_source = "api_request_state (endpoint)"
        if last_updated is None and core_sync and core_sync.get("last_updated_at"):
            last_updated = core_sync["last_updated_at"]
            timestamp_source = "resource_sync_state (core_season, dernier traitement)"
        age = _age_seconds(last_updated, now)
        status = _freshness_status(age, 24, 72) if last_updated is not None else "unknown"
        sync_counts = (core_sync or {}).get("status_counts") or {}
        incomplete_scopes = sum(
            total for sync_status, total in sync_counts.items()
            if sync_status != "complete"
        )
        if count == 0 or incomplete_scopes:
            status = "warning"
        message = (
            "Aucune ligne enregistrée." if count == 0
            else "Synchronisation de périmètres incomplète : couverture à contrôler."
            if incomplete_scopes
            else "Fraîcheur estimée depuis la dernière synchronisation réussie des périmètres ligue/saison."
            if timestamp_source != "table" and last_updated is not None
            else "Horodatage absent : fraîcheur non vérifiable." if last_updated is None
            else "Données récentes." if status == "healthy"
            else "Données à contrôler." if status in {"warning", "critical"}
            else "État non déterminé."
        )
        return {
            "name": table_name, "status": status, "count": count,
            "last_updated_at": str(last_updated) if last_updated is not None else None,
            "age_seconds": age, "message": message,
            "timestamp_source": timestamp_source if last_updated is not None else None,
        }
    except Exception as exc:
        return {
            "name": table_name, "status": "unknown", "count": None,
            "last_updated_at": None, "age_seconds": None,
            "message": f"Lecture impossible ({type(exc).__name__}).",
        }


def _archive_integrity(connection, inspector) -> dict:
    """Check stored fingerprint format and HTTP outcomes, not payload integrity."""
    table = "api_request_state"
    if not inspector.has_table(table):
        return {
            "status": "unknown", "checked": 0, "valid": None, "invalid": None,
            "latest_retrieved_at": None,
            "message": "Suivi des requêtes API absent.",
        }
    rows = connection.execute(text("""
        SELECT request_sha256, response_sha256, http_status, retrieved_at
        FROM api_request_state ORDER BY retrieved_at DESC
        LIMIT :limit
    """), {"limit": ARCHIVE_SAMPLE_SIZE}).mappings().all()
    if not rows:
        return {
            "status": "unknown", "checked": 0, "valid": None, "invalid": None,
            "latest_retrieved_at": None,
            "message": "Aucune requête API suivie.",
        }
    valid = 0
    invalid = 0
    http_failures = 0
    for row in rows:
        if row["http_status"] is not None and not 200 <= int(row["http_status"]) < 300:
            http_failures += 1
        hashes = (row["request_sha256"], row["response_sha256"])
        if all(
            isinstance(value, str)
            and len(value) == 64
            and all(char in "0123456789abcdef" for char in value.lower())
            for value in hashes
        ):
            valid += 1
        else:
            invalid += 1
    status = "critical" if invalid else "warning" if http_failures else "healthy"
    return {
        "status": status, "checked": len(rows), "valid": valid, "invalid": invalid,
        "http_failures": http_failures,
        "latest_retrieved_at": rows[0]["retrieved_at"],
        "latest_http_status": rows[0]["http_status"],
        "message": (
            "Format d'empreintes invalide détecté." if invalid
            else "Des requêtes API ont renvoyé une erreur HTTP." if http_failures
            else "Format des empreintes techniques vérifié (contenu brut non conservé)."
        ),
    }


def get_reliability_snapshot(*, now: dt.datetime | None = None) -> dict:
    """Return a read-only, explainable health snapshot without hiding failures."""
    current = now or _utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    current = current.astimezone(dt.timezone.utc)
    result = {
        "generated_at": current.isoformat(),
        "database": {"status": "unknown", "message": "Contrôle non effectué."},
        "tables": [],
        "api_archive": {"status": "unknown", "message": "Contrôle non effectué."},
        "core_sync": None,
        "latest_update": None,
        "sync_states": None,
        "overall_status": "unknown",
        "summary": {"healthy": 0, "warning": 0, "critical": 0, "unknown": 0},
    }
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1")).scalar_one()
            inspector = inspect(connection)
            result["database"] = {"status": "healthy", "message": "Connexion vérifiée."}
            core_sync = _core_sync_freshness(connection, inspector)
            result["core_sync"] = core_sync
            result["tables"] = [
                _table_check(connection, inspector, name, current, core_sync)
                for name in CORE_TABLES
            ]
            result["api_archive"] = _archive_integrity(connection, inspector)
            if inspector.has_table("update_log"):
                columns = _table_columns(inspector, "update_log")
                latest = _latest_value(connection, "update_log", columns, ("finished_at",))
                result["latest_update"] = str(latest) if latest is not None else None
            if inspector.has_table("resource_sync_state"):
                rows = connection.execute(text(
                    "SELECT status, COUNT(*) AS total FROM resource_sync_state "
                    "WHERE resource_type <> 'full_sync_control' GROUP BY status"
                )).all()
                result["sync_states"] = {str(status): int(total) for status, total in rows}
    except Exception as exc:
        result["database"] = {
            "status": "critical", "message": f"Contrôle impossible ({type(exc).__name__})."
        }
        result["tables"] = [
            {
                "name": name, "status": "unknown", "count": None,
                "last_updated_at": None, "age_seconds": None,
                "message": "Base inaccessible : mesure non vérifiable.",
            }
            for name in CORE_TABLES
        ]
        result["overall_status"] = "critical"
        result["summary"]["critical"] = 1
        return result

    checks = [result["database"], *result["tables"], result["api_archive"]]
    for check in checks:
        status = check.get("status", "unknown")
        result["summary"][status] = result["summary"].get(status, 0) + 1
    statuses = {check.get("status", "unknown") for check in checks}
    if "critical" in statuses:
        overall = "critical"
    elif "warning" in statuses:
        overall = "warning"
    elif "unknown" in statuses:
        overall = "warning"
    else:
        overall = "healthy"
    result["overall_status"] = overall
    return result
