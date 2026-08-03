import datetime
import json
import math
import os
import time

from sqlalchemy import text

from database.database import engine
from services import import_service, lineup_service, player_service, sync_registry
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


def _percent(value):
    try:
        return float(str(value).replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _upcoming_matches(days: int) -> list[dict]:
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT fixture_id, league_id, season, date, home_team_id, away_team_id
                FROM matches
                WHERE date >= CURRENT_TIMESTAMP
                  AND date <= datetime(CURRENT_TIMESTAMP, '+' || :days || ' days')
                  AND home_goals IS NULL AND away_goals IS NULL
                ORDER BY date ASC
                """
            ),
            {"days": int(days)},
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
    summary = {
        "total": len(rows),
        "downloaded": 0,
        "skipped": 0,
        "unavailable": 0,
        "errors": [],
        "quota_reached": False,
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
        if result in summary:
            summary[result] += 1
        if progress_callback:
            progress_callback(
                index,
                max(1, len(rows)),
                f"Conseils API : match {index}/{len(rows)}",
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


def run_full_sync(progress_callback=None) -> dict:
    """Complète les données manquantes et conserve un état de reprise en base."""
    import_service.init_db()
    sync_registry.ensure_table()
    config = import_service.get_auto_refresh_config()
    days = int(os.getenv("FULL_SYNC_UPCOMING_DAYS", "30"))
    pause = float(os.getenv("FULL_SYNC_PAUSE_SECONDS", str(config["pause"])))
    summary = {
        "core": "pending",
        "downloaded": 0,
        "skipped": 0,
        "unavailable": 0,
        "errors": [],
        "quota_reached": False,
        "partial": 0,
    }

    def progress(current, total, label):
        if progress_callback:
            progress_callback(current, max(1, total), label)

    progress(0, 100, "Vérification des championnats, équipes, matchs et classements...")
    seasons = list(range(config["start_season"], config["end_season"] + 1))
    missing_core = _missing_core_scopes(config["league_ids"], seasons)
    for index, (league_id, season) in enumerate(missing_core, start=1):
        import_service.import_leagues_cautious(
            [league_id],
            seasons=[season],
            pause=pause,
            max_retries=config["max_retries"],
        )
        progress(
            int((index / max(1, len(missing_core))) * 15),
            100,
            f"Données principales : ligue {league_id}, saison {season}",
        )
    remaining_core = _missing_core_scopes(config["league_ids"], seasons)
    summary["core"] = "complete" if not remaining_core else "partial"
    summary["core_downloaded"] = len(missing_core)
    summary["core_skipped"] = len(config["league_ids"]) * len(seasons) - len(missing_core)
    if remaining_core:
        summary["errors"].append(
            f"{len(remaining_core)} périmètre(s) principal(aux) restent indisponibles."
        )

    upcoming = _upcoming_matches(days)
    recent_ids = _recent_fixture_ids(upcoming)
    scopes = _player_scopes()
    total_items = max(1, len(upcoming) * 3 + len(recent_ids) + len(scopes))
    completed = 0

    def advance(label):
        nonlocal completed
        completed += 1
        progress(15 + int((completed / total_items) * 85), 100, label)

    for match in upcoming:
        fixture_id = int(match["fixture_id"])
        tasks = [
            (
                f"fixture-detail:{fixture_id}",
                "fixture_detail",
                lambda fid=fixture_id: client.get_fixture(fid),
                lambda item, fid=fixture_id: _save_fixture_details(fid, item),
                _fixture_details_present(fixture_id),
            ),
            (
                f"fixture-prediction:{fixture_id}",
                "fixture_prediction",
                lambda fid=fixture_id: client.get_predictions(fid),
                lambda item, fid=fixture_id: _save_prediction(fid, item),
                _prediction_present(fixture_id),
            ),
        ]
        for key, resource_type, fetch, save, already_present in tasks:
            if already_present:
                sync_registry.mark(key, resource_type, "complete", item_count=1, message="Déjà en base")
                result = "skipped"
            else:
                try:
                    result = _fetch_one(key, resource_type, fetch, save)
                except Exception as exc:
                    summary["errors"].append(f"{key}: {exc}")
                    if _is_quota_error(exc):
                        summary["quota_reached"] = True
                        return summary
                    result = "error"
                time.sleep(pause)
            if result in summary:
                summary[result] += 1
            advance(f"Match {fixture_id}: détails et prédiction")

        lineup_key = f"fixture-lineup:{fixture_id}"
        if _lineup_present(fixture_id):
            sync_registry.mark(lineup_key, "fixture_lineup", "complete", item_count=2, message="Déjà en base")
            result = "skipped"
        elif (
            (lineup_state := sync_registry.get(lineup_key))
            and lineup_state.get("status") != "complete"
            and not sync_registry.should_download(lineup_key, 12)
        ):
            result = "skipped"
        else:
            try:
                sync_registry.mark(lineup_key, "fixture_lineup", "running")
                lineup_result = lineup_service.sync_lineups(fixture_id)
                count = int(lineup_result.get("teams") or 0)
                status = "complete" if count >= 2 else ("partial" if count else "unavailable")
                sync_registry.mark(lineup_key, "fixture_lineup", status, item_count=count)
                result = "downloaded" if count >= 2 else ("partial" if count else "unavailable")
            except Exception as exc:
                sync_registry.mark(lineup_key, "fixture_lineup", "error", message=str(exc))
                summary["errors"].append(f"{lineup_key}: {exc}")
                if _is_quota_error(exc):
                    summary["quota_reached"] = True
                    return summary
                result = "error"
            time.sleep(pause)
        if result in summary:
            summary[result] += 1
        advance(f"Match {fixture_id}: composition")

    for fixture_id in recent_ids:
        key = f"fixture-players:{fixture_id}"
        try:
            if not sync_registry.should_download(key, 24):
                result = "skipped"
            else:
                sync_registry.mark(key, "fixture_players", "running")
                data = lineup_service.sync_fixture_players(fixture_id)
                count = int(data.get("players") or 0)
                status = "complete" if count else "unavailable"
                sync_registry.mark(key, "fixture_players", status, item_count=count)
                result = "downloaded" if count else "unavailable"
                time.sleep(pause)
        except Exception as exc:
            sync_registry.mark(key, "fixture_players", "error", message=str(exc))
            summary["errors"].append(f"{key}: {exc}")
            if _is_quota_error(exc):
                summary["quota_reached"] = True
                return summary
            result = "error"
        if result in summary:
            summary[result] += 1
        advance(f"Forme des joueurs: match {fixture_id}")

    for scope in scopes:
        league_id = int(scope["league_id"])
        season = int(scope["season"])
        key = f"season-players:{league_id}:{season}"
        existing = int(scope["player_count"] or 0)
        if existing:
            sync_registry.mark(key, "season_players", "complete", item_count=existing, message="Déjà en base")
            result = "skipped"
        elif (
            (player_state := sync_registry.get(key))
            and player_state.get("status") != "complete"
            and not sync_registry.should_download(key, 24)
        ):
            result = "skipped"
        else:
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
                summary["errors"].append(f"{key}: {exc}")
                if _is_quota_error(exc):
                    summary["quota_reached"] = True
                    return summary
                result = "error"
        if result in summary:
            summary[result] += 1
        advance(f"Joueurs: ligue {league_id}, saison {season}")

    progress(100, 100, "Toutes les informations disponibles ont été vérifiées")
    return summary
