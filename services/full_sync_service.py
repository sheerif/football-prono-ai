import datetime
import json
import math
import os
import time

from sqlalchemy import text

from database.database import engine
from services import import_service, lineup_service, player_service, sync_registry, xg_service
from services.api_football import ApiFootballClient


client = ApiFootballClient()


def _nonnegative_float_env(name: str, default: float) -> float:
    """Lit une temporisation sans laisser une variable invalide casser un job."""
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return max(0.0, float(default))
    return max(0.0, value) if math.isfinite(value) else max(0.0, float(default))


def _now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None).isoformat()


def _api_error(response: dict) -> str | None:
    errors = response.get("errors")
    if not errors:
        return None
    if isinstance(errors, dict):
        return " ; ".join(f"{key}: {value}" for key, value in errors.items())
    if isinstance(errors, list):
        return " ; ".join(str(value) for value in errors)
    return str(errors)


def _is_quota_error(error: Exception | str) -> bool:
    value = str(error).lower()
    return any(token in value for token in ("quota", "rate limit", "request limit", "too many requests", "429"))


def _daily_reserve() -> int:
    try:
        return max(0, int(os.getenv("FULL_SYNC_DAILY_RESERVE", "500")))
    except (TypeError, ValueError):
        return 500


def _request_count(api_client) -> int:
    try:
        return max(0, int(getattr(api_client, "request_count", 0) or 0))
    except (TypeError, ValueError):
        return 0


def _daily_reserve_reached(api_client, initial_request_count: int) -> bool:
    """Protège une réserve seulement après une réponse reçue dans ce passage."""
    if _request_count(api_client) <= initial_request_count:
        return False
    raw_remaining = (getattr(api_client, "last_rate_limit", {}) or {}).get(
        "daily_remaining"
    )
    try:
        return int(raw_remaining) <= _daily_reserve()
    except (TypeError, ValueError):
        return False


def _reserve_message() -> str:
    return (
        f"Quota journalier protégé : {_daily_reserve()} requêtes réservées "
        "aux prédictions et mises à jour courantes."
    )


def _resource_should_download(
    row: dict,
    state: dict | None,
    resource_key: str,
    retry_hours: int,
    *,
    terminal_unavailable_after_hours: int | None = None,
    repair_missing_complete: bool = False,
) -> bool:
    """Politique différentielle commune aux ressources liées à un match."""
    if not state:
        return True
    if state.get("status") == "complete":
        # Le stockage métier reste la source de vérité : l'appelant peut
        # demander une réparation si la ligne a disparu ou est incomplète.
        return repair_missing_complete
    if state.get("status") != "unavailable":
        return True

    if state.get("status") == "unavailable" and terminal_unavailable_after_hours:
        try:
            match_date = datetime.datetime.fromisoformat(
                str(row.get("date") or "").replace("Z", "+00:00")
            )
            if match_date.tzinfo is None:
                match_date = match_date.replace(tzinfo=datetime.UTC)
            age = datetime.datetime.now(datetime.UTC) - match_date.astimezone(
                datetime.UTC
            )
            if age.total_seconds() >= terminal_unavailable_after_hours * 3600:
                return False
        except (TypeError, ValueError):
            pass
    return sync_registry.should_download(resource_key, retry_hours)


def _xg_should_download(row: dict, state: dict | None, retry_hours: int) -> bool:
    try:
        grace_hours = max(24, int(os.getenv("XG_PUBLICATION_GRACE_HOURS", "72")))
    except (TypeError, ValueError):
        grace_hours = 72
    return _resource_should_download(
        row,
        state,
        f"fixture-statistics:{int(row['fixture_id'])}",
        retry_hours,
        terminal_unavailable_after_hours=grace_hours,
        repair_missing_complete=True,
    )


def _percent(value):
    try:
        return float(str(value).replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _registry_refresh_due(resource_key: str, refresh_hours: int) -> bool:
    state = sync_registry.get(resource_key)
    if not state or state.get("status") != "complete":
        return True
    try:
        updated_at = datetime.datetime.fromisoformat(str(state["updated_at"]))
        age = datetime.datetime.now(datetime.UTC).replace(tzinfo=None) - updated_at
        return age.total_seconds() >= max(1, int(refresh_hours)) * 3600
    except (KeyError, TypeError, ValueError):
        return True


def _upcoming_matches(days: int | None = None) -> list[dict]:
    date_limit = ""
    params = {}
    if days is not None:
        date_limit = "AND date <= datetime(CURRENT_TIMESTAMP, '+' || :days || ' days')"
        params["days"] = max(1, int(days))
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT fixture_id, league_id, season, date, home_team_id, away_team_id
                FROM matches
                WHERE date >= CURRENT_TIMESTAMP
                  AND home_goals IS NULL AND away_goals IS NULL
                  {date_limit}
                ORDER BY date ASC
                """
            ),
            params,
        ).mappings().all()
    return [dict(row) for row in rows]


def _all_matches() -> list[dict]:
    """Retourne tout le périmètre connu, nécessaire à une synchro exhaustive."""
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT fixture_id, league_id, season, date, home_team_id,
                       away_team_id, home_goals, away_goals
                FROM matches
                ORDER BY date DESC, fixture_id DESC
                """
            )
        ).mappings().all()
    return [dict(row) for row in rows]


def _player_scopes() -> list[dict]:
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT m.league_id, m.season, COUNT(*) AS match_count,
                       (SELECT COUNT(*) FROM player_statistics ps
                        WHERE ps.league_id = m.league_id AND ps.season = m.season) AS player_count
                FROM matches m
                GROUP BY m.league_id, m.season
                ORDER BY m.season DESC, m.league_id
                """
            )
        ).mappings().all()
    return [dict(row) for row in rows]


def overall_progress_snapshot() -> dict:
    """Mesure l'avancement durable à partir des lignes réellement en base."""
    import_service.init_db()
    config = import_service.get_auto_refresh_config()
    seasons = list(range(config["start_season"], config["end_season"] + 1))
    core_total = len(config["league_ids"]) * len(seasons)
    core_done = max(
        0,
        core_total - len(_missing_core_scopes(config["league_ids"], seasons)),
    )
    with engine.connect() as conn:
        def scalar(sql: str) -> int:
            return int(conn.execute(text(sql)).scalar() or 0)

        matches = scalar("SELECT COUNT(*) FROM matches")
        completed = scalar(
            "SELECT COUNT(*) FROM matches "
            "WHERE home_goals IS NOT NULL AND away_goals IS NOT NULL"
        )
        upcoming = scalar(
            "SELECT COUNT(*) FROM matches "
            "WHERE date >= CURRENT_TIMESTAMP "
            "AND home_goals IS NULL AND away_goals IS NULL"
        )
        details_done = scalar("SELECT COUNT(DISTINCT fixture_id) FROM fixture_api_details")
        lineups_done = scalar(
            "SELECT COUNT(*) FROM (SELECT fixture_id FROM fixture_lineups "
            "GROUP BY fixture_id HAVING COUNT(*) >= 2)"
        )
        predictions_done = scalar(
            "SELECT COUNT(DISTINCT p.fixture_id) FROM fixture_api_predictions p "
            "JOIN matches m ON m.fixture_id = p.fixture_id "
            "WHERE m.date >= CURRENT_TIMESTAMP "
            "AND m.home_goals IS NULL AND m.away_goals IS NULL"
        )
        fixture_players_done = scalar(
            "SELECT COUNT(DISTINCT fixture_id) FROM fixture_player_statistics"
        )
        season_scopes = scalar(
            "SELECT COUNT(*) FROM (SELECT league_id, season FROM matches "
            "GROUP BY league_id, season)"
        )
        season_players_done = scalar(
            "SELECT COUNT(*) FROM (SELECT m.league_id, m.season FROM matches m "
            "WHERE EXISTS (SELECT 1 FROM player_statistics p "
            "WHERE p.league_id = m.league_id AND p.season = m.season) "
            "GROUP BY m.league_id, m.season)"
        )
    xg_done = int(
        xg_service.coverage(config["league_ids"], seasons).get("available") or 0
    )
    total = max(
        1,
        core_total + matches * 2 + upcoming + completed * 2 + season_scopes,
    )
    current = min(
        total,
        core_done
        + details_done
        + lineups_done
        + predictions_done
        + fixture_players_done
        + season_players_done
        + xg_done,
    )
    return {
        "progress": current / total,
        "progress_current": current,
        "progress_total": total,
        "progress_label": (
            f"Couverture persistante : {current}/{total} ressources complètes"
        ),
    }


def _missing_core_scopes(league_ids: list[int], seasons: list[int]) -> list[tuple[int, int]]:
    missing = []
    with engine.begin() as conn:
        for league_id in league_ids:
            for season in seasons:
                match_count = conn.execute(
                    text(
                        "SELECT COUNT(*) FROM matches "
                        "WHERE league_id = :league_id AND season = :season"
                    ),
                    {"league_id": int(league_id), "season": int(season)},
                ).scalar()
                team_count = conn.execute(
                    text("SELECT COUNT(*) FROM teams WHERE league_id = :league_id"),
                    {"league_id": int(league_id)},
                ).scalar()
                standing_count = conn.execute(
                    text(
                        "SELECT COUNT(*) FROM standings "
                        "WHERE league_id = :league_id AND season = :season"
                    ),
                    {"league_id": int(league_id), "season": int(season)},
                ).scalar()
                if int(match_count or 0) == 0 or int(team_count or 0) == 0 or int(standing_count or 0) == 0:
                    missing.append((int(league_id), int(season)))
    return missing


def _recent_fixture_ids(upcoming: list[dict], per_team: int = 5) -> list[int]:
    ids: set[int] = set()
    with engine.begin() as conn:
        for match in upcoming:
            for team_id in (match["home_team_id"], match["away_team_id"]):
                rows = conn.execute(
                    text(
                        """
                        SELECT fixture_id FROM matches
                        WHERE (home_team_id = :team_id OR away_team_id = :team_id)
                          AND date < :before_date
                          AND home_goals IS NOT NULL AND away_goals IS NOT NULL
                        ORDER BY date DESC LIMIT :limit
                        """
                    ),
                    {
                        "team_id": int(team_id),
                        "before_date": str(match["date"]),
                        "limit": int(per_team),
                    },
                ).fetchall()
                ids.update(int(row[0]) for row in rows)
    return sorted(ids)


def _fixture_details_present(fixture_id: int) -> bool:
    with engine.begin() as conn:
        return bool(
            conn.execute(
                text("SELECT 1 FROM fixture_api_details WHERE fixture_id = :fixture_id"),
                {"fixture_id": int(fixture_id)},
            ).first()
        )


def _prediction_present(fixture_id: int) -> bool:
    with engine.begin() as conn:
        return bool(
            conn.execute(
                text("SELECT 1 FROM fixture_api_predictions WHERE fixture_id = :fixture_id"),
                {"fixture_id": int(fixture_id)},
            ).first()
        )


def _lineup_present(fixture_id: int) -> bool:
    with engine.begin() as conn:
        count = conn.execute(
            text("SELECT COUNT(*) FROM fixture_lineups WHERE fixture_id = :fixture_id"),
            {"fixture_id": int(fixture_id)},
        ).scalar()
    return int(count or 0) >= 2


def _fixture_players_present(fixture_id: int) -> bool:
    with engine.begin() as conn:
        count = conn.execute(
            text(
                "SELECT COUNT(*) FROM fixture_player_statistics "
                "WHERE fixture_id = :fixture_id"
            ),
            {"fixture_id": int(fixture_id)},
        ).scalar()
    return int(count or 0) > 0


def _save_fixture_details(fixture_id: int, item: dict) -> None:
    fixture = item.get("fixture") or {}
    league = item.get("league") or {}
    venue = fixture.get("venue") or {}
    status = fixture.get("status") or {}
    teams = item.get("teams") or {}
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO fixture_api_details (
                    fixture_id, league_id, season, round, venue, city, status_short,
                    home_logo, away_logo, league_logo, raw_json, updated_at
                ) VALUES (
                    :fixture_id, :league_id, :season, :round, :venue, :city, :status_short,
                    :home_logo, :away_logo, :league_logo, :raw_json, :updated_at
                )
                ON CONFLICT(fixture_id) DO UPDATE SET
                    league_id = excluded.league_id, season = excluded.season,
                    round = excluded.round, venue = excluded.venue, city = excluded.city,
                    status_short = excluded.status_short, home_logo = excluded.home_logo,
                    away_logo = excluded.away_logo, league_logo = excluded.league_logo,
                    raw_json = excluded.raw_json, updated_at = excluded.updated_at
                """
            ),
            {
                "fixture_id": int(fixture_id),
                "league_id": int(league.get("id")),
                "season": int(league.get("season")),
                "round": league.get("round"),
                "venue": venue.get("name"),
                "city": venue.get("city"),
                "status_short": status.get("short"),
                "home_logo": (teams.get("home") or {}).get("logo"),
                "away_logo": (teams.get("away") or {}).get("logo"),
                "league_logo": league.get("logo"),
                "raw_json": json.dumps(item, ensure_ascii=False, default=str),
                "updated_at": _now_iso(),
            },
        )


def _save_prediction(fixture_id: int, item: dict) -> None:
    prediction = item.get("predictions") or {}
    percentages = prediction.get("percent") or {}
    comparison = item.get("comparison") or {}
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO fixture_api_predictions (
                    fixture_id, advice, winner, home_probability, draw_probability,
                    away_probability, total_home, total_away, raw_json, updated_at
                ) VALUES (
                    :fixture_id, :advice, :winner, :home_probability, :draw_probability,
                    :away_probability, :total_home, :total_away, :raw_json, :updated_at
                )
                ON CONFLICT(fixture_id) DO UPDATE SET
                    advice = excluded.advice, winner = excluded.winner,
                    home_probability = excluded.home_probability,
                    draw_probability = excluded.draw_probability,
                    away_probability = excluded.away_probability,
                    total_home = excluded.total_home, total_away = excluded.total_away,
                    raw_json = excluded.raw_json, updated_at = excluded.updated_at
                """
            ),
            {
                "fixture_id": int(fixture_id),
                "advice": prediction.get("advice"),
                "winner": (prediction.get("winner") or {}).get("name"),
                "home_probability": _percent(percentages.get("home")),
                "draw_probability": _percent(percentages.get("draw")),
                "away_probability": _percent(percentages.get("away")),
                "total_home": (comparison.get("total") or {}).get("home"),
                "total_away": (comparison.get("total") or {}).get("away"),
                "raw_json": json.dumps(item, ensure_ascii=False, default=str),
                "updated_at": _now_iso(),
            },
        )


def _fetch_one(resource_key: str, resource_type: str, fetch, save, retry_hours: int = 12) -> str:
    state = sync_registry.get(resource_key)
    # The database remains the source of truth: a stale "complete" marker must
    # never prevent recovery when the corresponding row has been removed.
    if state and state.get("status") != "complete" and not sync_registry.should_download(
        resource_key, retry_hours
    ):
        return "skipped"
    sync_registry.mark(resource_key, resource_type, "running")
    try:
        response = fetch()
        error = _api_error(response)
        if error:
            raise RuntimeError(error)
        items = response.get("response") or []
        if not items:
            sync_registry.mark(resource_key, resource_type, "unavailable", message="Non publié par l’API")
            return "unavailable"
        save(items[0])
        sync_registry.mark(resource_key, resource_type, "complete", item_count=1)
        return "downloaded"
    except Exception as exc:
        sync_registry.mark(resource_key, resource_type, "error", message=str(exc))
        raise


def prediction_coverage(days: int | None = None) -> dict:
    """Mesure la couverture des conseils API sur les matchs futurs en base."""
    date_filter = ""
    params = {}
    if days is not None:
        date_filter = "AND m.date <= datetime(CURRENT_TIMESTAMP, '+' || :days || ' days')"
        params["days"] = max(1, int(days))
    with engine.begin() as conn:
        row = conn.execute(
            text(
                f"""
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN p.fixture_id IS NOT NULL THEN 1 ELSE 0 END) AS available
                FROM matches m
                LEFT JOIN fixture_api_predictions p ON p.fixture_id = m.fixture_id
                WHERE m.date >= CURRENT_TIMESTAMP
                  AND m.home_goals IS NULL AND m.away_goals IS NULL
                  {date_filter}
                """
            ),
            params,
        ).mappings().one()
    total = int(row["total"] or 0)
    available = int(row["available"] or 0)
    return {
        "total": total,
        "available": available,
        "missing": max(0, total - available),
        "percentage": round(available / total * 100, 1) if total else 0.0,
    }


def _sync_prediction_rows(
    rows,
    *,
    pause: float,
    retry_hours: int,
    progress_callback=None,
) -> dict:
    """Traite une liste figée de fixtures, ce qui rend la reprise testable."""
    initial_request_count = _request_count(client)
    summary = {
        "total": len(rows),
        "downloaded": 0,
        "skipped": 0,
        "unavailable": 0,
        "errors": [],
        "quota_reached": False,
        "duplicates_avoided": 0,
        "unavailable_deferred": 0,
    }
    for index, row in enumerate(rows, start=1):
        fixture_id = int(row["fixture_id"])
        key = f"fixture-prediction:{fixture_id}"
        if _prediction_present(fixture_id):
            sync_registry.mark(
                key,
                "fixture_prediction",
                "complete",
                item_count=1,
                message="Déjà en base",
            )
            result = "skipped"
            summary["duplicates_avoided"] += 1
        else:
            try:
                result = _fetch_one(
                    key,
                    "fixture_prediction",
                    lambda fid=fixture_id: client.get_predictions(fid),
                    lambda item, fid=fixture_id: _save_prediction(fid, item),
                    retry_hours=retry_hours,
                )
            except Exception as exc:
                summary["errors"].append(f"{key}: {exc}")
                if _is_quota_error(exc):
                    summary["quota_reached"] = True
                    break
                result = "error"
            if result != "skipped":
                time.sleep(pause)
            else:
                summary["unavailable_deferred"] += 1
        if result in summary:
            summary[result] += 1
        if progress_callback:
            progress_callback(
                index,
                max(1, len(rows)),
                f"Conseils API : {index}/{len(rows)} traités · "
                f"{summary['downloaded']} téléchargé(s) · "
                f"{summary['skipped']} évité(s)",
            )
    summary["api_calls"] = max(
        0,
        _request_count(client) - initial_request_count,
    )
    return summary


def sync_all_upcoming_predictions(
    *,
    days: int | None = None,
    pause: float | None = None,
    retry_hours: int = 12,
    progress_callback=None,
) -> dict:
    """Télécharge tous les conseils API publiés pour les fixtures futures.

    La synchronisation est incrémentale : les lignes déjà présentes ne
    consomment aucune requête et les réponses indisponibles sont retentées
    après le délai configuré.
    """
    import_service.init_db()
    sync_registry.ensure_table()
    pause = (
        _nonnegative_float_env("PREDICTION_SYNC_PAUSE_SECONDS", 0.25)
        if pause is None
        else max(0.0, float(pause))
    )
    date_filter = ""
    params = {}
    if days is not None:
        date_filter = "AND date <= datetime(CURRENT_TIMESTAMP, '+' || :days || ' days')"
        params["days"] = max(1, int(days))
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT fixture_id, date, league_id, season
                FROM matches
                WHERE date >= CURRENT_TIMESTAMP
                  AND home_goals IS NULL AND away_goals IS NULL
                  {date_filter}
                ORDER BY date ASC
                """
            ),
            params,
        ).mappings().all()

    summary = _sync_prediction_rows(
        rows,
        pause=pause,
        retry_hours=retry_hours,
        progress_callback=progress_callback,
    )
    summary["coverage"] = prediction_coverage(days)
    return summary


def _sync_xg_rows(
    rows,
    *,
    pause: float,
    retry_hours: int,
    progress_callback=None,
    sync_run_id: str | None = None,
) -> dict:
    """Synchronise une liste figée de matchs terminés, avec reprise sur quota."""
    sync_run_id = sync_run_id or xg_service.new_sync_run_id()
    initial_request_count = _request_count(client)
    summary = {
        "sync_run_id": sync_run_id,
        "total": len(rows),
        "downloaded": 0,
        "skipped": 0,
        "unavailable": 0,
        "errors": [],
        "quota_reached": False,
        "api_calls": 0,
        "duplicates_avoided": 0,
        "unavailable_deferred": 0,
        "daily_reserve": _daily_reserve(),
        "budget_reserved": False,
    }
    for index, row in enumerate(rows, start=1):
        fixture_id = int(row["fixture_id"])
        key = f"fixture-statistics:{fixture_id}"
        if xg_service.fixture_statistics_present(fixture_id):
            sync_registry.mark(
                key,
                "fixture_statistics",
                "complete",
                item_count=2,
                message="Déjà en base",
            )
            result = "skipped"
            summary["duplicates_avoided"] += 1
        elif not _xg_should_download(
            row,
            sync_registry.get(key),
            retry_hours,
        ):
            result = "skipped"
            summary["unavailable_deferred"] += 1
        else:
            if _daily_reserve_reached(client, initial_request_count):
                summary["errors"].append(f"{key}: {_reserve_message()}")
                summary["quota_reached"] = True
                summary["budget_reserved"] = True
                summary["checkpoint"] = key
                break
            requested_at = _now_iso()
            response = None
            try:
                sync_registry.mark(key, "fixture_statistics", "running")
                summary["api_calls"] += 1
                response = client.get_fixture_statistics(fixture_id)
                error = _api_error(response)
                if error:
                    raise RuntimeError(error)
                items = response.get("response") or []
                if not items:
                    xg_service.record_ingestion(
                        fixture_id,
                        sync_run_id=sync_run_id,
                        status="unavailable",
                        requested_at=requested_at,
                        payload=response,
                    )
                    sync_registry.mark(
                        key,
                        "fixture_statistics",
                        "unavailable",
                        message="Statistiques non publiées par l’API",
                    )
                    result = "unavailable"
                else:
                    count = xg_service.save_fixture_statistics(
                        fixture_id,
                        items,
                        sync_run_id=sync_run_id,
                        requested_at=requested_at,
                        audit_payload=response,
                    )
                    has_xg = xg_service.fixture_statistics_present(fixture_id)
                    sync_registry.mark(
                        key,
                        "fixture_statistics",
                        "complete" if has_xg else "unavailable",
                        item_count=count,
                        message=None if has_xg else "Statistiques publiées sans xG",
                    )
                    result = "downloaded" if has_xg else "unavailable"
            except Exception as exc:
                try:
                    xg_service.record_ingestion(
                        fixture_id,
                        sync_run_id=sync_run_id,
                        status="error",
                        requested_at=requested_at,
                        payload=response,
                        error=str(exc),
                    )
                except Exception as audit_exc:
                    summary["errors"].append(
                        f"fixture-statistics-audit:{fixture_id}: {audit_exc}"
                    )
                sync_registry.mark(
                    key, "fixture_statistics", "error", message=str(exc)
                )
                summary["errors"].append(f"{key}: {exc}")
                if _is_quota_error(exc):
                    summary["quota_reached"] = True
                    summary["checkpoint"] = key
                    break
                result = "error"
            if result != "skipped":
                time.sleep(pause)
        if result in summary:
            summary[result] += 1
        if progress_callback:
            progress_callback(
                index,
                max(1, len(rows)),
                f"xG différentiels : match {index}/{len(rows)} · "
                f"{summary['downloaded']} téléchargé(s) · "
                f"{summary['skipped']} évité(s) · "
                f"{summary['api_calls']} appel(s) API",
            )
    return summary


def sync_historical_xg(
    *,
    league_ids=None,
    seasons=None,
    max_matches: int | None = None,
    pause: float | None = None,
    retry_hours: int = 24,
    progress_callback=None,
) -> dict:
    """Télécharge les statistiques des matchs terminés, du plus récent au plus ancien."""
    import_service.init_db()
    sync_registry.ensure_table()
    coverage_before = xg_service.coverage(league_ids, seasons)
    pause = (
        _nonnegative_float_env("XG_SYNC_PAUSE_SECONDS", 0.25)
        if pause is None
        else max(0.0, float(pause))
    )
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
    limit_sql = ""
    if max_matches is not None:
        params["limit"] = max(1, int(max_matches))
        limit_sql = "LIMIT :limit"
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT m.fixture_id, m.date, m.league_id, m.season
                FROM matches m
                LEFT JOIN fixture_team_statistics s
                  ON s.fixture_id = m.fixture_id
                WHERE {' AND '.join(filters)}
                GROUP BY m.fixture_id, m.date, m.league_id, m.season
                HAVING COUNT(DISTINCT CASE
                    WHEN s.expected_goals IS NOT NULL
                     AND s.team_id IN (m.home_team_id, m.away_team_id)
                    THEN s.team_id END) < 2
                ORDER BY m.date DESC, m.fixture_id DESC
                {limit_sql}
                """
            ),
            params,
        ).mappings().all()
    summary = _sync_xg_rows(
        rows,
        pause=pause,
        retry_hours=retry_hours,
        progress_callback=progress_callback,
        sync_run_id=xg_service.new_sync_run_id(),
    )
    summary["coverage"] = xg_service.coverage(league_ids, seasons)
    summary["duplicates_avoided"] += int(
        coverage_before.get("available") or 0
    )
    return summary


def run_full_sync(progress_callback=None) -> dict:
    """Synchronise exhaustivement les données exploitées par l'application.

    Chaque ressource est validée dans SQLite dès sa réception. Une relance
    repart donc du registre persistant et ignore ce qui est déjà complet.
    """
    import_service.init_db()
    sync_registry.ensure_table()
    config = import_service.get_auto_refresh_config()
    pause = _nonnegative_float_env("FULL_SYNC_PAUSE_SECONDS", config["pause"])
    summary = {
        "sync_run_id": xg_service.new_sync_run_id(),
        "mode": "exhaustive",
        "core": "pending",
        "downloaded": 0,
        "skipped": 0,
        "unavailable": 0,
        "errors": [],
        "quota_reached": False,
        "partial": 0,
        "daily_reserve": _daily_reserve(),
        "budget_reserved": False,
    }
    tracked_clients = {
        "données principales": getattr(import_service, "client", None),
        "matchs/prédictions/xG": client,
        "compositions/statistiques joueurs": getattr(lineup_service, "client", None),
        "joueurs par saison": getattr(player_service, "client", None),
    }
    initial_request_counts = {
        label: _request_count(api_client)
        for label, api_client in tracked_clients.items()
    }

    def finalize() -> dict:
        calls = {
            label: max(
                0,
                _request_count(api_client)
                - initial_request_counts[label],
            )
            for label, api_client in tracked_clients.items()
        }
        summary["api_calls_by_family"] = calls
        summary["api_calls"] = sum(calls.values())
        summary["requests_avoided_at_least"] = (
            int(summary.get("skipped") or 0)
            + int(summary.get("core_skipped") or 0)
            + int(summary.get("xg_duplicates_avoided") or 0)
        )
        return summary

    def progress(current, total, label):
        if progress_callback:
            progress_callback(current, max(1, total), label)

    def add_result(result: str) -> None:
        if result in summary:
            summary[result] += 1

    def stop_for_quota(key: str, exc: Exception) -> dict:
        summary["errors"].append(f"{key}: {exc}")
        summary["quota_reached"] = True
        if "quota journalier protégé" in str(exc).casefold():
            summary["budget_reserved"] = True
        summary["checkpoint"] = key
        return finalize()

    progress(0, 100, "Vérification des championnats, équipes, matchs et classements...")
    seasons = list(range(config["start_season"], config["end_season"] + 1))
    missing_core = set(_missing_core_scopes(config["league_ids"], seasons))
    recent_count = max(1, int(config.get("recent_seasons") or 1))
    recent_seasons = set(seasons[-recent_count:])
    try:
        core_refresh_hours = max(1, int(os.getenv("CORE_SYNC_REFRESH_HOURS", "6")))
    except (TypeError, ValueError):
        core_refresh_hours = 6
    recent_due = {
        (int(league_id), int(season))
        for league_id in config["league_ids"]
        for season in recent_seasons
        if _registry_refresh_due(
            f"core:{int(league_id)}:{int(season)}", core_refresh_hours
        )
    }
    core_scopes = sorted(
        missing_core
        | recent_due
    )
    for index, (league_id, season) in enumerate(core_scopes, start=1):
        key = f"core:{league_id}:{season}"
        if _daily_reserve_reached(
            tracked_clients["données principales"],
            initial_request_counts["données principales"],
        ):
            return stop_for_quota(key, RuntimeError(_reserve_message()))
        sync_registry.mark(key, "core_season", "running")
        try:
            import_service.import_leagues_cautious(
                [league_id],
                seasons=[season],
                pause=pause,
                max_retries=config["max_retries"],
                force_refresh_seasons=[season] if season in recent_seasons else None,
            )
            sync_registry.mark(key, "core_season", "complete", item_count=1)
            summary["downloaded"] += 1
        except Exception as exc:
            sync_registry.mark(key, "core_season", "error", message=str(exc))
            if _is_quota_error(exc):
                return stop_for_quota(key, exc)
            summary["errors"].append(f"{key}: {exc}")
        progress(
            int((index / max(1, len(core_scopes))) * 15),
            100,
            f"Données principales : ligue {league_id}, saison {season} · "
            f"{summary['downloaded']} téléchargé(s)",
        )
    remaining_core = _missing_core_scopes(config["league_ids"], seasons)
    summary["core"] = "complete" if not remaining_core else "partial"
    summary["core_downloaded"] = len(core_scopes)
    summary["core_skipped"] = max(
        0, len(config["league_ids"]) * len(seasons) - len(core_scopes)
    )
    if remaining_core:
        summary["errors"].append(
            f"{len(remaining_core)} périmètre(s) principal(aux) restent indisponibles."
        )

    matches = _all_matches()
    upcoming = _upcoming_matches()
    completed_matches = [
        match
        for match in matches
        if match.get("home_goals") is not None and match.get("away_goals") is not None
    ]
    scopes = _player_scopes()

    # Les xG sont la donnée historique prioritaire. Ils sont traités avant les
    # autres endpoints afin que le quota quotidien serve d'abord à compléter
    # cette couverture. Le registre et la table de statistiques font office de
    # différence persistante entre deux passages.
    xg_result = sync_historical_xg(
        league_ids=config["league_ids"],
        seasons=seasons,
        max_matches=None,
        pause=pause,
        retry_hours=24,
        progress_callback=lambda current, total, label: progress(
            15 + int((current / max(1, total)) * 40),
            100,
            label,
        ),
    )
    summary["xg"] = xg_result
    summary["xg_api_calls"] = int(xg_result.get("api_calls") or 0)
    summary["xg_duplicates_avoided"] = int(
        xg_result.get("duplicates_avoided") or 0
    )
    summary["xg_unavailable_deferred"] = int(
        xg_result.get("unavailable_deferred") or 0
    )
    for result_key in ("downloaded", "skipped", "unavailable"):
        summary[result_key] += int(xg_result.get(result_key) or 0)
    summary["errors"].extend(xg_result.get("errors") or [])
    if xg_result.get("quota_reached"):
        summary["quota_reached"] = True
        summary["budget_reserved"] = bool(xg_result.get("budget_reserved"))
        summary["checkpoint"] = xg_result.get("checkpoint") or "fixture-statistics"
        summary["xg_coverage"] = xg_service.coverage(
            config["league_ids"], seasons
        )
        return finalize()

    total_items = max(
        1,
        len(matches) * 2
        + len(upcoming)
        + len(completed_matches)
        + len(scopes),
    )
    completed = 0

    def advance(label):
        nonlocal completed
        completed += 1
        progress(
            55 + int((completed / total_items) * 45),
            100,
            f"{label} · {summary['downloaded']} téléchargé(s) · "
            f"{summary['skipped']} évité(s)",
        )

    for match in matches:
        fixture_id = int(match["fixture_id"])
        detail_key = f"fixture-detail:{fixture_id}"
        if _fixture_details_present(fixture_id):
            sync_registry.mark(
                detail_key,
                "fixture_detail",
                "complete",
                item_count=1,
                message="Déjà en base",
            )
            result = "skipped"
        elif not _resource_should_download(
            match,
            sync_registry.get(detail_key),
            detail_key,
            12,
            terminal_unavailable_after_hours=72,
            repair_missing_complete=True,
        ):
            result = "skipped"
        else:
            if _daily_reserve_reached(
                client, initial_request_counts["matchs/prédictions/xG"]
            ):
                return stop_for_quota(detail_key, RuntimeError(_reserve_message()))
            try:
                result = _fetch_one(
                    detail_key,
                    "fixture_detail",
                    lambda fid=fixture_id: client.get_fixture(fid),
                    lambda item, fid=fixture_id: _save_fixture_details(fid, item),
                )
            except Exception as exc:
                if _is_quota_error(exc):
                    return stop_for_quota(detail_key, exc)
                summary["errors"].append(f"{detail_key}: {exc}")
                result = "error"
            if result != "skipped":
                time.sleep(pause)
        add_result(result)
        advance(f"Match {fixture_id}: détails")

        lineup_key = f"fixture-lineup:{fixture_id}"
        if _lineup_present(fixture_id):
            sync_registry.mark(lineup_key, "fixture_lineup", "complete", item_count=2, message="Déjà en base")
            result = "skipped"
        elif not _resource_should_download(
            match,
            sync_registry.get(lineup_key),
            lineup_key,
            12,
            terminal_unavailable_after_hours=72,
            repair_missing_complete=True,
        ):
            result = "skipped"
        else:
            if _daily_reserve_reached(
                tracked_clients["compositions/statistiques joueurs"],
                initial_request_counts["compositions/statistiques joueurs"],
            ):
                return stop_for_quota(lineup_key, RuntimeError(_reserve_message()))
            try:
                sync_registry.mark(lineup_key, "fixture_lineup", "running")
                lineup_result = lineup_service.sync_lineups(fixture_id)
                count = int(lineup_result.get("teams") or 0)
                status = "complete" if count >= 2 else ("partial" if count else "unavailable")
                sync_registry.mark(lineup_key, "fixture_lineup", status, item_count=count)
                result = "downloaded" if count >= 2 else ("partial" if count else "unavailable")
            except Exception as exc:
                sync_registry.mark(lineup_key, "fixture_lineup", "error", message=str(exc))
                if _is_quota_error(exc):
                    return stop_for_quota(lineup_key, exc)
                summary["errors"].append(f"{lineup_key}: {exc}")
                result = "error"
            time.sleep(pause)
        add_result(result)
        advance(f"Match {fixture_id}: composition")

    for match in upcoming:
        fixture_id = int(match["fixture_id"])
        key = f"fixture-prediction:{fixture_id}"
        if _prediction_present(fixture_id):
            sync_registry.mark(
                key,
                "fixture_prediction",
                "complete",
                item_count=1,
                message="Déjà en base",
            )
            result = "skipped"
        else:
            if _daily_reserve_reached(
                client, initial_request_counts["matchs/prédictions/xG"]
            ):
                return stop_for_quota(key, RuntimeError(_reserve_message()))
            try:
                result = _fetch_one(
                    key,
                    "fixture_prediction",
                    lambda fid=fixture_id: client.get_predictions(fid),
                    lambda item, fid=fixture_id: _save_prediction(fid, item),
                )
            except Exception as exc:
                if _is_quota_error(exc):
                    return stop_for_quota(key, exc)
                summary["errors"].append(f"{key}: {exc}")
                result = "error"
            if result != "skipped":
                time.sleep(pause)
        add_result(result)
        advance(f"Match {fixture_id}: prédiction API")

    for match in completed_matches:
        fixture_id = int(match["fixture_id"])
        key = f"fixture-players:{fixture_id}"
        try:
            if _fixture_players_present(fixture_id):
                sync_registry.mark(
                    key,
                    "fixture_players",
                    "complete",
                    message="Déjà en base",
                )
                result = "skipped"
            elif not _resource_should_download(
                match,
                sync_registry.get(key),
                key,
                24,
                terminal_unavailable_after_hours=72,
                repair_missing_complete=True,
            ):
                result = "skipped"
            else:
                if _daily_reserve_reached(
                    tracked_clients["compositions/statistiques joueurs"],
                    initial_request_counts["compositions/statistiques joueurs"],
                ):
                    return stop_for_quota(key, RuntimeError(_reserve_message()))
                sync_registry.mark(key, "fixture_players", "running")
                data = lineup_service.sync_fixture_players(fixture_id)
                count = int(data.get("players") or 0)
                status = "complete" if count else "unavailable"
                sync_registry.mark(key, "fixture_players", status, item_count=count)
                result = "downloaded" if count else "unavailable"
                time.sleep(pause)
        except Exception as exc:
            sync_registry.mark(key, "fixture_players", "error", message=str(exc))
            if _is_quota_error(exc):
                return stop_for_quota(key, exc)
            summary["errors"].append(f"{key}: {exc}")
            result = "error"
        add_result(result)
        advance(f"Forme des joueurs: match {fixture_id}")

    for scope in scopes:
        league_id = int(scope["league_id"])
        season = int(scope["season"])
        key = f"season-players:{league_id}:{season}"
        existing = int(scope["player_count"] or 0)
        player_state = sync_registry.get(key)
        if existing and player_state and player_state.get("status") == "complete":
            sync_registry.mark(key, "season_players", "complete", item_count=existing, message="Déjà en base")
            result = "skipped"
        elif player_state and player_state.get("status") == "unavailable" and season < max(seasons):
            result = "skipped"
        elif (
            player_state
            and player_state.get("status") != "complete"
            and not sync_registry.should_download(key, 24)
        ):
            result = "skipped"
        else:
            if _daily_reserve_reached(
                tracked_clients["joueurs par saison"],
                initial_request_counts["joueurs par saison"],
            ):
                return stop_for_quota(key, RuntimeError(_reserve_message()))
            try:
                sync_registry.mark(key, "season_players", "running")
                data = player_service.sync_players(
                    league_id,
                    season,
                    progress_callback=lambda page, pages, page_label: progress(
                        15
                        + int(
                            (
                                completed
                                + page / max(1, pages)
                            )
                            / total_items
                            * 85
                        ),
                        100,
                        page_label,
                    ),
                )
                count = int(data.get("statistics") or 0)
                if data.get("truncated"):
                    status = "partial"
                else:
                    status = "complete" if count else "unavailable"
                sync_registry.mark(
                    key,
                    "season_players",
                    status,
                    item_count=count,
                    page_count=int(data.get("pages") or 0),
                    metadata=data,
                )
                result = "partial" if data.get("truncated") and count else ("downloaded" if count else "unavailable")
                time.sleep(pause)
            except Exception as exc:
                sync_registry.mark(key, "season_players", "error", message=str(exc))
                if _is_quota_error(exc):
                    return stop_for_quota(key, exc)
                summary["errors"].append(f"{key}: {exc}")
                result = "error"
        add_result(result)
        advance(f"Joueurs: ligue {league_id}, saison {season}")

    summary["prediction_coverage"] = prediction_coverage()
    summary["xg_coverage"] = xg_service.coverage(
        config["league_ids"],
        seasons,
    )
    progress(100, 100, "Toutes les informations disponibles ont été vérifiées")
    return finalize()
