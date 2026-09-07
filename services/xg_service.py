"""Ingestion et lecture des xG observés fournis par API-Football."""

from __future__ import annotations

import datetime
import hashlib
import json
import math
import uuid

import pandas as pd
from sqlalchemy import text

from database.database import engine


SOURCE = "API-Football"
ENDPOINT = "/fixtures/statistics"
SOURCE_FIELD = "expected_goals"


def _now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None).isoformat()


def _number(value, *, nonnegative: bool = False) -> float | None:
    try:
        number = float(str(value).replace("%", "").strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or (nonnegative and number < 0):
        return None
    return number


def _statistic_key(value) -> str:
    return str(value or "").strip().casefold().replace(" ", "_")


def new_sync_run_id() -> str:
    return str(uuid.uuid4())


def _serialized_payload(payload) -> tuple[str | None, str | None]:
    if payload is None:
        return None, None
    body = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return body, hashlib.sha256(body.encode("utf-8")).hexdigest()


def _insert_audit(
    conn,
    *,
    fixture_id: int,
    sync_run_id: str,
    status: str,
    requested_at: str,
    payload=None,
    item_count: int = 0,
    has_xg: bool = False,
    error: str | None = None,
) -> tuple[int, str | None, str]:
    response_json, payload_sha256 = _serialized_payload(payload)
    completed_at = _now_iso()
    result = conn.execute(
        text(
            """
            INSERT INTO xg_ingestion_audit (
                sync_run_id, fixture_id, source, endpoint, request_json, status,
                requested_at, completed_at, item_count, has_xg, payload_sha256,
                response_json, error
            ) VALUES (
                :sync_run_id, :fixture_id, :source, :endpoint, :request_json, :status,
                :requested_at, :completed_at, :item_count, :has_xg, :payload_sha256,
                :response_json, :error
            )
            """
        ),
        {
            "sync_run_id": str(sync_run_id),
            "fixture_id": int(fixture_id),
            "source": SOURCE,
            "endpoint": ENDPOINT,
            "request_json": json.dumps(
                {"fixture": int(fixture_id)}, sort_keys=True, separators=(",", ":")
            ),
            "status": str(status),
            "requested_at": str(requested_at),
            "completed_at": completed_at,
            "item_count": int(item_count or 0),
            "has_xg": bool(has_xg),
            "payload_sha256": payload_sha256,
            "response_json": response_json,
            "error": error,
        },
    )
    return int(result.lastrowid), payload_sha256, completed_at


def record_ingestion(
    fixture_id: int,
    *,
    sync_run_id: str,
    status: str,
    requested_at: str,
    payload=None,
    item_count: int = 0,
    has_xg: bool = False,
    error: str | None = None,
) -> int:
    """Ajoute une preuve immuable pour une tentative sans valeurs à persister."""
    with engine.begin() as conn:
        ingestion_id, _, _ = _insert_audit(
            conn,
            fixture_id=fixture_id,
            sync_run_id=sync_run_id,
            status=status,
            requested_at=requested_at,
            payload=payload,
            item_count=item_count,
            has_xg=has_xg,
            error=error,
        )
    return ingestion_id


def parse_fixture_statistics(items: list[dict]) -> list[dict]:
    """Transforme la réponse hétérogène de l'API en lignes stables."""
    parsed = []
    for item in items or []:
        team = item.get("team") or {}
        try:
            team_id = int(team.get("id"))
        except (TypeError, ValueError):
            continue
        statistics = item.get("statistics") or []
        values = {
            _statistic_key(statistic.get("type")): statistic.get("value")
            for statistic in statistics
            if isinstance(statistic, dict)
        }
        parsed.append(
            {
                "team_id": team_id,
                "team_name": team.get("name"),
                "expected_goals": _number(
                    values.get("expected_goals"), nonnegative=True
                ),
                "goals_prevented": _number(values.get("goals_prevented")),
                "raw_json": json.dumps(item, ensure_ascii=False, default=str),
            }
        )
    return parsed


def save_fixture_statistics(
    fixture_id: int,
    items: list[dict],
    *,
    sync_run_id: str | None = None,
    requested_at: str | None = None,
    audit_payload=None,
) -> int:
    """Enregistre atomiquement toutes les équipes publiées pour une fixture."""
    rows = parse_fixture_statistics(items)
    if not rows:
        return 0
    sync_run_id = sync_run_id or new_sync_run_id()
    requested_at = requested_at or _now_iso()
    has_xg = any(row["expected_goals"] is not None for row in rows)
    with engine.begin() as conn:
        match = conn.execute(
            text(
                "SELECT home_team_id, away_team_id FROM matches "
                "WHERE fixture_id = :fixture_id"
            ),
            {"fixture_id": int(fixture_id)},
        ).mappings().first()
        if not match:
            raise ValueError(f"Match {fixture_id} absent de la base locale.")
        ingestion_id, payload_sha256, completed_at = _insert_audit(
            conn,
            fixture_id=fixture_id,
            sync_run_id=sync_run_id,
            status="available" if has_xg else "unavailable",
            requested_at=requested_at,
            payload=items if audit_payload is None else audit_payload,
            item_count=len(rows),
            has_xg=has_xg,
        )
        for row in rows:
            is_home = None
            if row["team_id"] == int(match["home_team_id"]):
                is_home = True
            elif row["team_id"] == int(match["away_team_id"]):
                is_home = False
            conn.execute(
                text(
                    """
                    INSERT INTO fixture_team_statistics (
                        fixture_id, team_id, team_name, is_home, expected_goals,
                        goals_prevented, source, source_endpoint, source_field,
                        retrieved_at, payload_sha256, ingestion_id, raw_json, updated_at
                    ) VALUES (
                        :fixture_id, :team_id, :team_name, :is_home, :expected_goals,
                        :goals_prevented, :source, :source_endpoint, :source_field,
                        :retrieved_at, :payload_sha256, :ingestion_id, :raw_json, :updated_at
                    )
                    ON CONFLICT(fixture_id, team_id) DO UPDATE SET
                        team_name = excluded.team_name,
                        is_home = excluded.is_home,
                        expected_goals = excluded.expected_goals,
                        goals_prevented = excluded.goals_prevented,
                        source = excluded.source,
                        source_endpoint = excluded.source_endpoint,
                        source_field = excluded.source_field,
                        retrieved_at = excluded.retrieved_at,
                        payload_sha256 = excluded.payload_sha256,
                        ingestion_id = excluded.ingestion_id,
                        raw_json = excluded.raw_json,
                        updated_at = excluded.updated_at
                    """
                ),
                {
                    "fixture_id": int(fixture_id),
                    **row,
                    "is_home": is_home,
                    "source": SOURCE,
                    "source_endpoint": ENDPOINT,
                    "source_field": SOURCE_FIELD,
                    "retrieved_at": completed_at,
                    "payload_sha256": payload_sha256,
                    "ingestion_id": ingestion_id,
                    "updated_at": completed_at,
                },
            )
    return len(rows)


def recent_audit(limit: int = 25) -> pd.DataFrame:
    """Retourne les dernières preuves d'ingestion, sans exposer de secret."""
    try:
        return pd.read_sql(
            text(
                """
                SELECT a.id, a.sync_run_id, a.fixture_id, a.source, a.endpoint,
                       a.request_json, a.status, a.requested_at, a.completed_at,
                       a.item_count, a.has_xg, a.payload_sha256, a.error,
                       home.name AS home_name, away.name AS away_name
                FROM xg_ingestion_audit a
                LEFT JOIN matches m ON m.fixture_id = a.fixture_id
                LEFT JOIN teams home ON home.id = m.home_team_id
                LEFT JOIN teams away ON away.id = m.away_team_id
                ORDER BY a.id DESC
                LIMIT :limit
                """
            ),
            engine,
            params={"limit": max(1, int(limit))},
        )
    except Exception:
        return pd.DataFrame()


def fixture_statistics_present(fixture_id: int) -> bool:
    """Vrai uniquement si les deux équipes du match possèdent un xG."""
    try:
        with engine.connect() as conn:
            count = conn.execute(
                text(
                    """
                    SELECT COUNT(DISTINCT s.team_id)
                    FROM fixture_team_statistics s
                    JOIN matches m ON m.fixture_id = s.fixture_id
                    WHERE s.fixture_id = :fixture_id
                      AND s.expected_goals IS NOT NULL
                      AND s.team_id IN (m.home_team_id, m.away_team_id)
                    """
                ),
                {"fixture_id": int(fixture_id)},
            ).scalar()
        return int(count or 0) >= 2
    except Exception:
        return False


def coverage(league_ids=None, seasons=None) -> dict:
    """Mesure la couverture des statistiques sur les matchs terminés."""
    filters = ["m.home_goals IS NOT NULL", "m.away_goals IS NOT NULL"]
    params = {}
    if league_ids:
        placeholders = ",".join(f":league_{i}" for i, _ in enumerate(league_ids))
        filters.append(f"m.league_id IN ({placeholders})")
        params.update({f"league_{i}": int(value) for i, value in enumerate(league_ids)})
    if seasons:
        placeholders = ",".join(f":season_{i}" for i, _ in enumerate(seasons))
        filters.append(f"m.season IN ({placeholders})")
        params.update({f"season_{i}": int(value) for i, value in enumerate(seasons)})
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    f"""
                    SELECT COUNT(DISTINCT m.fixture_id) AS total,
                           COUNT(DISTINCT s.fixture_id) AS downloaded,
                           COUNT(DISTINCT CASE WHEN home_xg.expected_goals IS NOT NULL
                                                    AND away_xg.expected_goals IS NOT NULL
                                              THEN m.fixture_id END) AS available
                    FROM matches m
                    LEFT JOIN fixture_team_statistics s
                      ON s.fixture_id = m.fixture_id
                    LEFT JOIN fixture_team_statistics home_xg
                      ON home_xg.fixture_id = m.fixture_id
                     AND home_xg.team_id = m.home_team_id
                    LEFT JOIN fixture_team_statistics away_xg
                      ON away_xg.fixture_id = m.fixture_id
                     AND away_xg.team_id = m.away_team_id
                    WHERE {' AND '.join(filters)}
                    """
                ),
                params,
            ).mappings().one()
    except Exception:
        row = {"total": 0, "downloaded": 0, "available": 0}
    total = int(row["total"] or 0)
    downloaded = int(row["downloaded"] or 0)
    available = int(row["available"] or 0)
    return {
        "total": total,
        "downloaded": downloaded,
        "available": available,
        "partial": max(0, downloaded - available),
        "missing": max(0, total - available),
        "percentage": round(available / total * 100, 1) if total else 0.0,
    }


def summarize_team(matches_df: pd.DataFrame, team_id: int, limit: int = 8) -> dict:
    """Calcule xG/xGA sur une fenêtre déjà limitée dans le temps par l'appelant."""
    total = 0
    samples = []
    retrieved_at = []
    if not matches_df.empty:
        rows = matches_df[
            (matches_df["home_team_id"] == int(team_id))
            | (matches_df["away_team_id"] == int(team_id))
        ].copy()
        if "date" in rows:
            rows["_xg_date"] = pd.to_datetime(rows["date"], errors="coerce", utc=True)
            rows = rows.sort_values("_xg_date", ascending=False)
        rows = rows.head(max(1, int(limit)))
        total = len(rows)
        for _, row in rows.iterrows():
            home = int(row["home_team_id"]) == int(team_id)
            own = row.get("home_xg" if home else "away_xg")
            against = row.get("away_xg" if home else "home_xg")
            if pd.notna(own) and pd.notna(against):
                samples.append((float(own), float(against)))
                provenance = row.get(
                    "home_xg_retrieved_at" if home else "away_xg_retrieved_at"
                )
                if pd.notna(provenance):
                    retrieved_at.append(str(provenance))
    if not samples:
        return {
            "matches": 0,
            "window_matches": total,
            "coverage": 0.0,
            "xg_for": None,
            "xg_against": None,
            "difference": None,
            "source": SOURCE,
            "latest_retrieved_at": None,
        }
    xg_for = sum(item[0] for item in samples) / len(samples)
    xg_against = sum(item[1] for item in samples) / len(samples)
    return {
        "matches": len(samples),
        "window_matches": total,
        "coverage": round(len(samples) / max(1, total), 3),
        "xg_for": round(xg_for, 2),
        "xg_against": round(xg_against, 2),
        "difference": round(xg_for - xg_against, 2),
        "source": SOURCE,
        "latest_retrieved_at": max(retrieved_at) if retrieved_at else None,
    }
