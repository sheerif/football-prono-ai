import datetime
import hashlib
import json
import pandas as pd
import re
import streamlit as st
from sqlalchemy import text

from components import ui
from database.database import engine
from services.api_football import ApiFootballClient
from services import prediction_helpers, prediction_service
from services import schema_guard
from services.season_format import season_period


api_client = ApiFootballClient()
PREVIEW_CACHE_VERSION = "score-outcome-v2"


def _load_upcoming_matches(days_ahead: int, league_ids: list[int] | None = None) -> pd.DataFrame:
    params = {"days_ahead": int(days_ahead)}
    league_filter = ""
    if league_ids:
        placeholders = ",".join([f":league_{index}" for index, _ in enumerate(league_ids)])
        league_filter = f"AND m.league_id IN ({placeholders})"
        params.update({f"league_{index}": int(league_id) for index, league_id in enumerate(league_ids)})

    query = text(
        f"""
        SELECT
            m.fixture_id,
            m.league_id,
            COALESCE(l.name, 'Championnat ' || m.league_id) AS league_name,
            COALESCE(l.country, '') AS country,
            m.season,
            m.date,
            m.home_team_id,
            COALESCE(home.name, 'Equipe ' || m.home_team_id) AS home_name,
            m.away_team_id,
            COALESCE(away.name, 'Equipe ' || m.away_team_id) AS away_name,
            COALESCE(m.status, 'Programmé') AS status
        FROM matches m
        LEFT JOIN leagues l ON l.id = m.league_id
        LEFT JOIN teams home ON home.id = m.home_team_id
        LEFT JOIN teams away ON away.id = m.away_team_id
        WHERE m.date >= CURRENT_TIMESTAMP
          AND m.date <= datetime(CURRENT_TIMESTAMP, '+' || :days_ahead || ' days')
          AND m.home_goals IS NULL
          AND m.away_goals IS NULL
          {league_filter}
        ORDER BY l.name, m.date, home.name, away.name
        """
    )
    try:
        return pd.read_sql(query, engine, params=params)
    except Exception:
        return pd.DataFrame()


def _load_leagues_with_upcoming() -> pd.DataFrame:
    try:
        return pd.read_sql(
            text(
                """
                SELECT
                    m.league_id AS id,
                    COALESCE(l.name, 'Championnat ' || m.league_id) AS name,
                    COALESCE(l.country, '') AS country,
                    COUNT(*) AS upcoming_count,
                    MIN(m.date) AS first_match
                FROM matches m
                LEFT JOIN leagues l ON l.id = m.league_id
                WHERE m.date >= CURRENT_TIMESTAMP
                  AND m.home_goals IS NULL
                  AND m.away_goals IS NULL
                GROUP BY m.league_id, l.name, l.country
                ORDER BY first_match, name
                """
            ),
            engine,
        )
    except Exception:
        return pd.DataFrame(columns=["id", "name", "country", "upcoming_count", "first_match"])


def _load_prediction_context(league_id: int, match_date) -> pd.DataFrame:
    query = text(
        """
        SELECT *
        FROM matches
        WHERE league_id = :league_id
          AND date < :match_date
        ORDER BY date DESC
        """
    )
    try:
        return pd.read_sql(query, engine, params={"league_id": int(league_id), "match_date": str(match_date)})
    except Exception:
        return pd.DataFrame()


def _format_datetime(value) -> str:
    timestamp = pd.to_datetime(value, errors="coerce")
    if pd.isna(timestamp):
        return "Date inconnue"
    return timestamp.strftime("%d/%m/%Y %H:%M UTC")


def _format_date(value) -> str:
    timestamp = pd.to_datetime(value, errors="coerce")
    if pd.isna(timestamp):
        return "Date inconnue"
    return timestamp.strftime("%d/%m/%Y")


def _format_time(value) -> str:
    timestamp = pd.to_datetime(value, errors="coerce")
    if pd.isna(timestamp):
        return ""
    return timestamp.strftime("%H:%M")


def _format_probability(value) -> str:
    try:
        return f"{float(value):.2f} %"
    except Exception:
        return "Non calculé"


def _display_text(value, fallback: str = "") -> str:
    if value is None:
        return fallback
    try:
        if pd.isna(value):
            return fallback
    except Exception:
        pass
    text_value = str(value).strip()
    return text_value or fallback


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None).isoformat()


def _json_dump(value) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)
    except Exception:
        return str(value)


def _json_changed(old_value, new_value: str) -> bool:
    if not old_value:
        return True
    try:
        return json.loads(old_value) != json.loads(new_value)
    except Exception:
        return str(old_value) != str(new_value)


def _percent_to_float(value):
    if value is None:
        return None
    try:
        return float(str(value).replace("%", "").strip())
    except Exception:
        return None


def _round_label(value) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "Journée non précisée"
    if raw.lower().startswith("regular season -"):
        number = raw.rsplit("-", 1)[-1].strip()
        if number.isdigit():
            return f"Journée {number}"
    return raw


def _round_sort_key(value):
    raw = str(value or "")
    match = re.search(r"(\d+)$", raw)
    if match:
        return int(match.group(1))
    number = raw.rsplit("-", 1)[-1].strip()
    if number.isdigit():
        return int(number)
    return 999


def _status_label(value) -> str:
    labels = {
        "NS": "A venir",
        "TBD": "Horaire à confirmer",
        "PST": "Reporté",
        "CANC": "Annulé",
        "SUSP": "Suspendu",
        "FT": "Terminé",
    }
    raw = str(value or "").strip()
    return labels.get(raw, raw or "A venir")


def _load_cached_fixture_details(league_id: int, season: int, fixture_ids: tuple[int, ...]) -> dict[int, dict]:
    if not fixture_ids:
        return {}
    placeholders = ",".join([f":fixture_{index}" for index, _ in enumerate(fixture_ids)])
    params = {"league_id": int(league_id), "season": int(season)}
    params.update({f"fixture_{index}": int(fixture_id) for index, fixture_id in enumerate(fixture_ids)})
    try:
        rows = pd.read_sql(
            text(
                f"""
                SELECT *
                FROM fixture_api_details
                WHERE league_id = :league_id
                  AND season = :season
                  AND fixture_id IN ({placeholders})
                """
            ),
            engine,
            params=params,
        )
    except Exception:
        return {}

    details = {}
    for row in rows.itertuples():
        details[int(row.fixture_id)] = {
            "api_round": row.round,
            "api_venue": row.venue,
            "api_city": row.city,
            "api_status_short": row.status_short,
            "api_home_logo": row.home_logo,
            "api_away_logo": row.away_logo,
            "api_league_logo": row.league_logo,
        }
    return details


def _save_fixture_details(league_id: int, season: int, api_items: list[dict]) -> dict:
    now = _utc_now_iso()
    rows = []
    for item in api_items:
        fixture = item.get("fixture") or {}
        fixture_id = fixture.get("id")
        if not fixture_id:
            continue
        league = item.get("league") or {}
        venue = fixture.get("venue") or {}
        status = fixture.get("status") or {}
        teams = item.get("teams") or {}
        rows.append(
            {
                "fixture_id": int(fixture_id),
                "league_id": int(league_id),
                "season": int(season),
                "round": league.get("round"),
                "venue": venue.get("name"),
                "city": venue.get("city"),
                "status_short": status.get("short"),
                "home_logo": (teams.get("home") or {}).get("logo"),
                "away_logo": (teams.get("away") or {}).get("logo"),
                "league_logo": league.get("logo"),
                "raw_json": _json_dump(item),
                "updated_at": now,
            }
        )
    if not rows:
        return {"inserted": 0, "updated": 0, "unchanged": 0}

    fixture_ids = tuple(row["fixture_id"] for row in rows)
    placeholders = ",".join([f":fixture_{index}" for index, _ in enumerate(fixture_ids)])
    params = {f"fixture_{index}": int(fixture_id) for index, fixture_id in enumerate(fixture_ids)}
    with engine.begin() as conn:
        existing_rows = conn.execute(
            text(f"SELECT fixture_id, raw_json FROM fixture_api_details WHERE fixture_id IN ({placeholders})"),
            params,
        ).fetchall()
    existing = {int(row[0]): row[1] for row in existing_rows}
    rows_to_write = []
    stats = {"inserted": 0, "updated": 0, "unchanged": 0}
    for row in rows:
        old_raw = existing.get(int(row["fixture_id"]))
        if old_raw is None:
            stats["inserted"] += 1
            rows_to_write.append(row)
        elif _json_changed(old_raw, row["raw_json"]):
            stats["updated"] += 1
            rows_to_write.append(row)
        else:
            stats["unchanged"] += 1

    if not rows_to_write:
        return stats

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO fixture_api_details (
                    fixture_id, league_id, season, round, venue, city, status_short,
                    home_logo, away_logo, league_logo, raw_json, updated_at
                )
                VALUES (
                    :fixture_id, :league_id, :season, :round, :venue, :city, :status_short,
                    :home_logo, :away_logo, :league_logo, :raw_json, :updated_at
                )
                ON CONFLICT(fixture_id) DO UPDATE SET
                    league_id = excluded.league_id,
                    season = excluded.season,
                    round = excluded.round,
                    venue = excluded.venue,
                    city = excluded.city,
                    status_short = excluded.status_short,
                    home_logo = excluded.home_logo,
                    away_logo = excluded.away_logo,
                    league_logo = excluded.league_logo,
                    raw_json = excluded.raw_json,
                    updated_at = excluded.updated_at
                """
            ),
            rows_to_write,
        )
    return stats


def _sync_fixture_details(upcoming: pd.DataFrame, progress_callback=None) -> dict:
    schema_guard.ensure_fixture_api_cache_tables()
    grouped = list(upcoming.groupby(["league_id", "season"]))
    total_groups = max(1, len(grouped))
    totals = {"inserted": 0, "updated": 0, "unchanged": 0}
    for group_index, ((league_id, season), _) in enumerate(grouped, start=1):
        if progress_callback:
            progress_callback(group_index - 1, total_groups, f"Comparaison API: ligue {league_id}, saison {season}")
        try:
            response = api_client.get_fixtures(int(league_id), int(season))
        except Exception:
            continue
        stats = _save_fixture_details(int(league_id), int(season), response.get("response") or [])
        for key in totals:
            totals[key] += stats.get(key, 0)
    if progress_callback:
        progress_callback(total_groups, total_groups, "Détails des matchs synchronisés")
    return totals


def _fixture_detail_complete(detail: dict | None) -> bool:
    if not detail:
        return False
    return bool(
        _display_text(detail.get("api_round"))
        and _display_text(detail.get("api_home_logo"))
        and _display_text(detail.get("api_away_logo"))
    )


def _sync_missing_fixture_details(upcoming: pd.DataFrame, progress_callback=None) -> dict:
    schema_guard.ensure_fixture_api_cache_tables()
    grouped = list(upcoming.groupby(["league_id", "season"]))
    total_groups = max(1, len(grouped))
    totals = {"inserted": 0, "updated": 0, "unchanged": 0, "skipped": 0}

    for group_index, ((league_id, season), group) in enumerate(grouped, start=1):
        if progress_callback:
            progress_callback(group_index - 1, total_groups, f"Vérification SQLite: ligue {league_id}, saison {season}")

        fixture_ids = tuple(int(value) for value in group["fixture_id"].dropna().tolist())
        cached_details = _load_cached_fixture_details(int(league_id), int(season), fixture_ids)
        missing_or_incomplete = [
            fixture_id
            for fixture_id in fixture_ids
            if not _fixture_detail_complete(cached_details.get(fixture_id))
        ]
        if not missing_or_incomplete:
            totals["skipped"] += len(fixture_ids)
            continue

        if progress_callback:
            progress_callback(
                group_index - 1,
                total_groups,
                f"Complément API: {len(missing_or_incomplete)} détail(s) manquant(s)",
            )
        try:
            response = api_client.get_fixtures(int(league_id), int(season))
        except Exception:
            continue
        stats = _save_fixture_details(int(league_id), int(season), response.get("response") or [])
        for key in ["inserted", "updated", "unchanged"]:
            totals[key] += stats.get(key, 0)

    if progress_callback:
        progress_callback(total_groups, total_groups, "Détails, journées et logos vérifiés")
    return totals


def _load_cached_prediction(fixture_id: int) -> dict:
    try:
        row = pd.read_sql(
            text("SELECT * FROM fixture_api_predictions WHERE fixture_id = :fixture_id"),
            engine,
            params={"fixture_id": int(fixture_id)},
        )
    except Exception:
        return {}
    if row.empty:
        return {}
    item = row.iloc[0]
    return {
        "api_advice": item.get("advice"),
        "api_winner": item.get("winner"),
        "api_home_probability": item.get("home_probability"),
        "api_draw_probability": item.get("draw_probability"),
        "api_away_probability": item.get("away_probability"),
        "api_total_home": item.get("total_home"),
        "api_total_away": item.get("total_away"),
    }


def _save_prediction(fixture_id: int, prediction_item: dict) -> str:
    predictions = prediction_item.get("predictions") or {}
    percent = predictions.get("percent") or {}
    comparison = prediction_item.get("comparison") or {}
    raw_json = _json_dump(prediction_item)
    try:
        existing = pd.read_sql(
            text("SELECT raw_json FROM fixture_api_predictions WHERE fixture_id = :fixture_id"),
            engine,
            params={"fixture_id": int(fixture_id)},
        )
    except Exception:
        existing = pd.DataFrame()
    if not existing.empty and not _json_changed(existing.iloc[0]["raw_json"], raw_json):
        return "unchanged"
    status = "inserted" if existing.empty else "updated"
    row = {
        "fixture_id": int(fixture_id),
        "advice": predictions.get("advice"),
        "winner": (predictions.get("winner") or {}).get("name"),
        "home_probability": _percent_to_float(percent.get("home")),
        "draw_probability": _percent_to_float(percent.get("draw")),
        "away_probability": _percent_to_float(percent.get("away")),
        "total_home": (comparison.get("total") or {}).get("home"),
        "total_away": (comparison.get("total") or {}).get("away"),
        "raw_json": raw_json,
        "updated_at": _utc_now_iso(),
    }
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO fixture_api_predictions (
                    fixture_id, advice, winner, home_probability, draw_probability,
                    away_probability, total_home, total_away, raw_json, updated_at
                )
                VALUES (
                    :fixture_id, :advice, :winner, :home_probability, :draw_probability,
                    :away_probability, :total_home, :total_away, :raw_json, :updated_at
                )
                ON CONFLICT(fixture_id) DO UPDATE SET
                    advice = excluded.advice,
                    winner = excluded.winner,
                    home_probability = excluded.home_probability,
                    draw_probability = excluded.draw_probability,
                    away_probability = excluded.away_probability,
                    total_home = excluded.total_home,
                    total_away = excluded.total_away,
                    raw_json = excluded.raw_json,
                    updated_at = excluded.updated_at
                """
            ),
            row,
        )
    return status


def _api_prediction(fixture_id: int, force_refresh: bool = False) -> dict:
    schema_guard.ensure_fixture_api_cache_tables()
    cached = _load_cached_prediction(fixture_id)
    if cached and not force_refresh:
        return cached

    try:
        response = api_client.get_predictions(int(fixture_id))
    except Exception:
        return cached
    items = response.get("response") or []
    if not items:
        return cached

    _save_prediction(int(fixture_id), items[0])
    return _load_cached_prediction(fixture_id)


def _enrich_with_api_details(upcoming: pd.DataFrame, force_refresh: bool = False, progress_callback=None) -> pd.DataFrame:
    if upcoming.empty:
        return upcoming

    enriched = upcoming.copy()
    for column in [
        "api_round",
        "api_venue",
        "api_city",
        "api_status_short",
        "api_home_logo",
        "api_away_logo",
        "api_league_logo",
    ]:
        enriched[column] = ""

    grouped = list(enriched.groupby(["league_id", "season"]))
    total_groups = max(1, len(grouped))
    for group_index, ((league_id, season), group) in enumerate(grouped, start=1):
        if progress_callback:
            progress_callback(group_index - 1, total_groups, f"Lecture SQLite: ligue {league_id}, saison {season}")
        fixture_ids = tuple(int(value) for value in group["fixture_id"].dropna().tolist())
        details = _load_cached_fixture_details(int(league_id), int(season), fixture_ids)
        if not details:
            continue
        for index, row in group.iterrows():
            fixture_details = details.get(int(row["fixture_id"])) or {}
            for key, value in fixture_details.items():
                enriched.at[index, key] = value or ""
    if progress_callback:
        progress_callback(total_groups, total_groups, "Détails chargés depuis SQLite")
    return enriched


def _enrich_with_api_predictions(
    previews: pd.DataFrame,
    fixture_ids: list[int],
    enabled: bool,
    limit: int,
    force_refresh: bool = False,
    progress_callback=None,
) -> pd.DataFrame:
    enriched = previews.copy()
    for column in ["Conseil API", "Probas API 1/N/2", "Comparaison API"]:
        enriched[column] = ""
    if not enabled:
        return enriched

    limited_ids = [int(fixture_id) for fixture_id in fixture_ids[: int(limit)]]
    allowed_ids = set(limited_ids)
    total_predictions = max(1, len(limited_ids))
    loaded_predictions = 0
    for index, row in enriched.iterrows():
        fixture_id = int(row["fixture_id"])
        if fixture_id not in allowed_ids:
            enriched.at[index, "Conseil API"] = "Non chargé"
            continue
        if progress_callback:
            progress_callback(loaded_predictions, total_predictions, f"Conseil API: match {fixture_id}")
        prediction = _api_prediction(fixture_id, force_refresh=force_refresh)
        loaded_predictions += 1
        if not prediction:
            enriched.at[index, "Conseil API"] = "Indisponible"
            continue
        enriched.at[index, "Conseil API"] = prediction.get("api_advice") or prediction.get("api_winner") or "Indisponible"
        enriched.at[index, "Probas API 1/N/2"] = (
            f"{_format_probability(prediction.get('api_home_probability'))} / "
            f"{_format_probability(prediction.get('api_draw_probability'))} / "
            f"{_format_probability(prediction.get('api_away_probability'))}"
        )
        if prediction.get("api_total_home") or prediction.get("api_total_away"):
            enriched.at[index, "Comparaison API"] = (
                f"Domicile {prediction.get('api_total_home') or '-'} / "
                f"Extérieur {prediction.get('api_total_away') or '-'}"
            )
    if progress_callback:
        progress_callback(total_predictions, total_predictions, "Conseils API chargés")
    return enriched


def _prefetch_api_predictions(fixture_ids: list[int], force_refresh: bool = False, progress_callback=None) -> dict:
    unique_ids = []
    seen = set()
    for fixture_id in fixture_ids:
        value = int(fixture_id)
        if value not in seen:
            unique_ids.append(value)
            seen.add(value)

    if not force_refresh:
        unique_ids = [fixture_id for fixture_id in unique_ids if not _load_cached_prediction(fixture_id)]

    total = max(1, len(unique_ids))
    stats = {"inserted": 0, "updated": 0, "unchanged": 0, "unavailable": 0}
    for index, fixture_id in enumerate(unique_ids, start=1):
        if progress_callback:
            progress_callback(index - 1, total, f"Comparaison prédiction API: match {fixture_id}")
        before = _load_cached_prediction(fixture_id)
        prediction = _api_prediction(fixture_id, force_refresh=True)
        after = _load_cached_prediction(fixture_id)
        if not after:
            stats["unavailable"] += 1
        elif not before:
            stats["inserted"] += 1
        elif before != after:
            stats["updated"] += 1
        else:
            stats["unchanged"] += 1
    if progress_callback:
        progress_callback(total, total, "Conseils API disponibles")
    return stats


def _confidence_label(confidence: float) -> str:
    if confidence >= 65:
        return "signal fort"
    if confidence >= 55:
        return "signal intéressant"
    if confidence >= 45:
        return "match ouvert"
    return "signal prudent"


def _favorite_from_prediction(prediction: dict, home_name: str, away_name: str) -> tuple[str, str, float]:
    outcomes = [
        ("1", home_name, prediction["home_probability"]),
        ("N", "Match nul", prediction["draw_probability"]),
        ("2", away_name, prediction["away_probability"]),
    ]
    code, label, probability = max(outcomes, key=lambda item: item[2])
    return code, label, float(probability)


def _score_matches_outcome(score: dict, outcome_code: str) -> bool:
    try:
        home_goals = int(score.get("Buts domicile"))
        away_goals = int(score.get("Buts extérieur"))
    except Exception:
        raw_score = str(score.get("Score") or "")
        match = re.match(r"^\s*(\d+)\s*-\s*(\d+)\s*$", raw_score)
        if not match:
            return False
        home_goals = int(match.group(1))
        away_goals = int(match.group(2))

    if outcome_code == "1":
        return home_goals > away_goals
    if outcome_code == "2":
        return away_goals > home_goals
    if outcome_code == "N":
        return home_goals == away_goals
    return False


def _scores_for_outcome(scores: list[dict], outcome_code: str) -> list[dict]:
    if not scores:
        return []
    compatible_score = next((score for score in scores if _score_matches_outcome(score, outcome_code)), None)
    return [compatible_score or scores[0]]


def _summary_sentence(home_name: str, away_name: str, prediction: dict, details: dict, scores: list[dict]) -> str:
    code, favorite, probability = _favorite_from_prediction(prediction, home_name, away_name)
    confidence = float(prediction.get("confidence") or probability)
    label = _confidence_label(confidence)
    score_text = ""
    if scores:
        best_score = scores[0]
        score_text = f" Score probable: {best_score['Score']} ({best_score['Probabilité']} %)."

    form_gap = details["home_form_score"] - details["away_form_score"]
    if abs(form_gap) >= 10:
        form_text = f"la forme récente avantage {'le domicile' if form_gap > 0 else 'l’extérieur'}"
    else:
        form_text = "les formes récentes sont proches"

    return (
        f"Lecture {code}: {favorite} ressort à {_format_probability(probability)} "
        f"avec {confidence:.2f} % de confiance ({label}); {form_text}. "
        f"Probabilités 1/N/2: {_format_probability(prediction['home_probability'])}, "
        f"{_format_probability(prediction['draw_probability'])}, {_format_probability(prediction['away_probability'])}."
        f"{score_text}"
    )


def _clean_hash_value(value):
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value)


def _context_signature(context_df: pd.DataFrame) -> dict:
    if context_df.empty:
        return {"count": 0}
    completed = context_df.dropna(subset=["home_goals", "away_goals"])
    if completed.empty:
        return {"count": 0}
    fixture_ids = pd.to_numeric(completed["fixture_id"], errors="coerce") if "fixture_id" in completed else pd.Series(dtype="float64")
    return {
        "count": int(len(completed)),
        "last_date": _clean_hash_value(completed["date"].max()),
        "fixture_sum": int(fixture_ids.fillna(0).sum()),
        "home_goals_sum": int(pd.to_numeric(completed["home_goals"], errors="coerce").fillna(0).sum()),
        "away_goals_sum": int(pd.to_numeric(completed["away_goals"], errors="coerce").fillna(0).sum()),
    }


def _preview_source_hash(match, context_df: pd.DataFrame) -> str:
    payload = {
        "cache_version": PREVIEW_CACHE_VERSION,
        "fixture_id": int(match.fixture_id),
        "league_id": int(match.league_id),
        "season": _clean_hash_value(match.season),
        "date": _clean_hash_value(match.date),
        "home_team_id": int(match.home_team_id),
        "away_team_id": int(match.away_team_id),
        "home_name": _clean_hash_value(match.home_name),
        "away_name": _clean_hash_value(match.away_name),
        "league_name": _clean_hash_value(match.league_name),
        "status": _clean_hash_value(match.status),
        "api_round": _clean_hash_value(getattr(match, "api_round", "")),
        "api_venue": _clean_hash_value(getattr(match, "api_venue", "")),
        "api_city": _clean_hash_value(getattr(match, "api_city", "")),
        "api_status_short": _clean_hash_value(getattr(match, "api_status_short", "")),
        "api_home_logo": _clean_hash_value(getattr(match, "api_home_logo", "")),
        "api_away_logo": _clean_hash_value(getattr(match, "api_away_logo", "")),
        "context": _context_signature(context_df),
    }
    return hashlib.sha256(_json_dump(payload).encode("utf-8")).hexdigest()


def _load_cached_previews(fixture_ids: list[int]) -> dict[int, dict]:
    unique_ids = []
    seen = set()
    for fixture_id in fixture_ids:
        value = int(fixture_id)
        if value not in seen:
            unique_ids.append(value)
            seen.add(value)
    if not unique_ids:
        return {}

    placeholders = ",".join([f":fixture_{index}" for index, _ in enumerate(unique_ids)])
    params = {f"fixture_{index}": fixture_id for index, fixture_id in enumerate(unique_ids)}
    try:
        rows = pd.read_sql(
            text(f"SELECT * FROM fixture_match_previews WHERE fixture_id IN ({placeholders})"),
            engine,
            params=params,
        )
    except Exception:
        return {}

    return {int(row.fixture_id): row._asdict() for row in rows.itertuples(index=False)}


def _cached_preview_to_row(item: dict) -> dict:
    return {
        "fixture_id": int(item.get("fixture_id")),
        "Date et heure": item.get("date_time") or "",
        "Date": item.get("date_label") or "",
        "Heure": item.get("time_label") or "",
        "Saison sportive": item.get("season_label") or "",
        "Championnat": item.get("league_name") or "",
        "Journée": item.get("round_label") or "Journée non précisée",
        "Round API": item.get("round_api") or "",
        "Stade": item.get("venue") or "",
        "Ville": item.get("city") or "",
        "Logo domicile": item.get("home_logo") or "",
        "Logo extérieur": item.get("away_logo") or "",
        "Match": item.get("match_label") or "",
        "Domicile": item.get("home_name") or "",
        "Extérieur": item.get("away_name") or "",
        "Statut": item.get("status") or "",
        "Pronostic": item.get("pronostic") or "",
        "Confiance": item.get("confidence") or "",
        "Score probable": item.get("score_probable") or "",
        "Résumé": item.get("summary") or "",
    }


def _save_match_preview(preview: dict, source_hash: str) -> None:
    row = {
        "fixture_id": int(preview["fixture_id"]),
        "source_hash": source_hash,
        "date_time": preview.get("Date et heure"),
        "date_label": preview.get("Date"),
        "time_label": preview.get("Heure"),
        "season_label": preview.get("Saison sportive"),
        "league_name": preview.get("Championnat"),
        "round_label": preview.get("Journée"),
        "round_api": preview.get("Round API"),
        "venue": preview.get("Stade"),
        "city": preview.get("Ville"),
        "home_logo": preview.get("Logo domicile"),
        "away_logo": preview.get("Logo extérieur"),
        "match_label": preview.get("Match"),
        "home_name": preview.get("Domicile"),
        "away_name": preview.get("Extérieur"),
        "status": preview.get("Statut"),
        "pronostic": preview.get("Pronostic"),
        "confidence": preview.get("Confiance"),
        "score_probable": preview.get("Score probable"),
        "summary": preview.get("Résumé"),
        "updated_at": _utc_now_iso(),
    }
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO fixture_match_previews (
                    fixture_id, source_hash, date_time, date_label, time_label, season_label,
                    league_name, round_label, round_api, venue, city, home_logo, away_logo,
                    match_label, home_name, away_name, status, pronostic, confidence,
                    score_probable, summary, updated_at
                )
                VALUES (
                    :fixture_id, :source_hash, :date_time, :date_label, :time_label, :season_label,
                    :league_name, :round_label, :round_api, :venue, :city, :home_logo, :away_logo,
                    :match_label, :home_name, :away_name, :status, :pronostic, :confidence,
                    :score_probable, :summary, :updated_at
                )
                ON CONFLICT(fixture_id) DO UPDATE SET
                    source_hash = excluded.source_hash,
                    date_time = excluded.date_time,
                    date_label = excluded.date_label,
                    time_label = excluded.time_label,
                    season_label = excluded.season_label,
                    league_name = excluded.league_name,
                    round_label = excluded.round_label,
                    round_api = excluded.round_api,
                    venue = excluded.venue,
                    city = excluded.city,
                    home_logo = excluded.home_logo,
                    away_logo = excluded.away_logo,
                    match_label = excluded.match_label,
                    home_name = excluded.home_name,
                    away_name = excluded.away_name,
                    status = excluded.status,
                    pronostic = excluded.pronostic,
                    confidence = excluded.confidence,
                    score_probable = excluded.score_probable,
                    summary = excluded.summary,
                    updated_at = excluded.updated_at
                """
            ),
            row,
        )


def _build_match_preview(match, context_df: pd.DataFrame) -> dict:
    home_name = str(match.home_name)
    away_name = str(match.away_name)
    home_team_id = int(match.home_team_id)
    away_team_id = int(match.away_team_id)

    completed = context_df.dropna(subset=["home_goals", "away_goals"]) if not context_df.empty else context_df
    team_rows = completed[
        (completed["home_team_id"].isin([home_team_id, away_team_id]))
        | (completed["away_team_id"].isin([home_team_id, away_team_id]))
    ]

    if completed.empty or team_rows.empty:
        return {
            "fixture_id": int(match.fixture_id),
            "Date et heure": _format_datetime(match.date),
            "Date": _format_date(match.date),
            "Heure": _format_time(match.date),
            "Saison sportive": season_period(match.season),
            "Championnat": match.league_name,
            "Journée": _round_label(getattr(match, "api_round", "") or ""),
            "Round API": getattr(match, "api_round", "") or "",
            "Stade": getattr(match, "api_venue", "") or "",
            "Ville": getattr(match, "api_city", "") or "",
            "Logo domicile": getattr(match, "api_home_logo", "") or "",
            "Logo extérieur": getattr(match, "api_away_logo", "") or "",
            "Match": f"{home_name} - {away_name}",
            "Domicile": home_name,
            "Extérieur": away_name,
            "Statut": _status_label(getattr(match, "api_status_short", "") or match.status),
            "Pronostic": "Données insuffisantes",
            "Confiance": "",
            "Score probable": "",
            "Résumé": "Pas assez de matchs terminés dans la base pour produire une lecture fiable sur cette affiche.",
        }

    team_options = {
        home_team_id: home_name,
        away_team_id: away_name,
    }
    prediction, _, _, details = prediction_helpers.predict_match(context_df, home_team_id, away_team_id)
    score_prediction = prediction_service.predict_scorelines(
        context_df,
        home_team_id,
        away_team_id,
        home_form_score=details["home_form_score"] / 100,
        away_form_score=details["away_form_score"] / 100,
        top_n=12,
    )
    code, favorite, probability = _favorite_from_prediction(prediction, home_name, away_name)
    scores = _scores_for_outcome(score_prediction.get("scores", []), code)
    score_label = scores[0]["Score"] if scores else ""
    return {
        "fixture_id": int(match.fixture_id),
        "Date et heure": _format_datetime(match.date),
        "Date": _format_date(match.date),
        "Heure": _format_time(match.date),
        "Saison sportive": season_period(match.season),
        "Championnat": match.league_name,
        "Journée": _round_label(getattr(match, "api_round", "") or ""),
        "Round API": getattr(match, "api_round", "") or "",
        "Stade": getattr(match, "api_venue", "") or "",
        "Ville": getattr(match, "api_city", "") or "",
        "Logo domicile": getattr(match, "api_home_logo", "") or "",
        "Logo extérieur": getattr(match, "api_away_logo", "") or "",
        "Match": f"{home_name} - {away_name}",
        "Domicile": home_name,
        "Extérieur": away_name,
        "Statut": _status_label(getattr(match, "api_status_short", "") or match.status),
        "Pronostic": f"{code} - {favorite}",
        "Confiance": _format_probability(prediction["confidence"]),
        "Score probable": score_label,
        "Résumé": _summary_sentence(home_name, away_name, prediction, details, scores),
    }


def _build_previews(upcoming: pd.DataFrame, max_per_league: int, progress_callback=None) -> pd.DataFrame:
    schema_guard.ensure_fixture_api_cache_tables()
    rows = []
    limited_groups = [
        (league_id, league_matches.sort_values("date").head(int(max_per_league)))
        for league_id, league_matches in upcoming.groupby("league_id", sort=False)
    ]
    fixture_ids = [
        int(match.fixture_id)
        for _, limited_matches in limited_groups
        for match in limited_matches.itertuples()
    ]
    cached_previews = _load_cached_previews(fixture_ids)
    total_matches = max(1, sum(len(league_matches) for _, league_matches in limited_groups))
    processed_matches = 0
    reused_count = 0
    generated_count = 0
    for league_id, limited_matches in limited_groups:
        context_cache = {}
        for match in limited_matches.itertuples():
            if progress_callback:
                progress_callback(processed_matches, total_matches, f"Lecture SQLite: {match.home_name} - {match.away_name}")
            context_key = (int(league_id), str(match.date))
            if context_key not in context_cache:
                context_cache[context_key] = _load_prediction_context(int(league_id), match.date)
            source_hash = _preview_source_hash(match, context_cache[context_key])
            cached = cached_previews.get(int(match.fixture_id))
            if cached and cached.get("source_hash") == source_hash:
                rows.append(_cached_preview_to_row(cached))
                reused_count += 1
            else:
                preview = _build_match_preview(match, context_cache[context_key])
                _save_match_preview(preview, source_hash)
                rows.append(preview)
                generated_count += 1
            processed_matches += 1
    if progress_callback:
        progress_callback(
            total_matches,
            total_matches,
            f"Résumés SQLite prêts: {reused_count} déjà en base, {generated_count} mis à jour",
        )
    return pd.DataFrame(rows)


def _update_progress(progress_bar, status_slot, current: int, total: int, label: str):
    progress_bar.progress(min(1.0, current / max(1, total)))
    status_slot.caption(label)


def _api_probability_line(prediction: dict) -> str:
    if not prediction:
        return ""
    return (
        f"{_format_probability(prediction.get('api_home_probability'))} / "
        f"{_format_probability(prediction.get('api_draw_probability'))} / "
        f"{_format_probability(prediction.get('api_away_probability'))}"
    )


def _render_match_detail(row: pd.Series, force_api_refresh: bool = False):
    venue = _display_text(row.get("Stade"), "Stade non renseigné")
    city = _display_text(row.get("Ville"))
    st.markdown(f"### {row['Domicile']} - {row['Extérieur']}")
    logo_cols = st.columns([0.16, 1, 0.16])
    if row.get("Logo domicile"):
        logo_cols[0].image(row["Logo domicile"], width=52)
    logo_cols[1].markdown(
        f"**{row['Championnat']} - {row['Journée']}**  \n"
        f"{row['Date et heure']}  \n"
        f"{venue}"
        f"{', ' + city if city else ''}"
    )
    if row.get("Logo extérieur"):
        logo_cols[2].image(row["Logo extérieur"], width=52)

    signal_cols = st.columns(3)
    signal_cols[0].metric("Pronostic", row.get("Pronostic") or "-")
    signal_cols[1].metric("Confiance", row.get("Confiance") or "-")
    signal_cols[2].metric("Score", row.get("Score probable") or "-")

    st.info(row.get("Résumé") or "Résumé non disponible.")

    with st.spinner("Chargement du conseil API si nécessaire..."):
        api_prediction = _api_prediction(int(row["fixture_id"]), force_refresh=force_api_refresh)

    if api_prediction:
        st.markdown("#### Conseil API")
        advice = api_prediction.get("api_advice") or api_prediction.get("api_winner") or "Non disponible"
        st.write(f"**{advice}**")
        st.caption(f"Probabilités API 1/N/2: {_api_probability_line(api_prediction)}")
        if api_prediction.get("api_total_home") or api_prediction.get("api_total_away"):
            st.caption(
                f"Comparaison API: domicile {api_prediction.get('api_total_home') or '-'} / "
                f"extérieur {api_prediction.get('api_total_away') or '-'}"
            )

    meta_cols = st.columns(3)
    meta_cols[0].caption("Statut")
    meta_cols[0].write(f"**{row.get('Statut') or '-'}**")
    meta_cols[1].caption("Stade")
    meta_cols[1].write(f"**{venue}**")
    meta_cols[2].caption("Ville")
    meta_cols[2].write(f"**{city or 'Non renseignée'}**")


def _render_match_card(row: pd.Series, force_api_refresh: bool = False):
    fixture_id = int(row["fixture_id"])
    venue = _display_text(row.get("Stade"), "Stade non renseigné")
    city = _display_text(row.get("Ville"))
    with st.container(border=True):
        logo_cols = st.columns([0.16, 1, 0.16])
        if row.get("Logo domicile"):
            logo_cols[0].image(row["Logo domicile"], width=44)
        logo_cols[1].markdown(f"### {row['Domicile']} - {row['Extérieur']}")
        if row.get("Logo extérieur"):
            logo_cols[2].image(row["Logo extérieur"], width=44)

        st.caption(
            f"{row['Championnat']} - {row['Journée']} | "
            f"{row['Date']} à {row['Heure']} UTC"
        )
        st.write(f"**{venue}**{', ' + city if city else ''}")

        signal_cols = st.columns(3)
        signal_cols[0].caption("Pronostic")
        signal_cols[0].write(f"**{row.get('Pronostic') or '-'}**")
        signal_cols[1].caption("Confiance")
        signal_cols[1].write(f"**{row.get('Confiance') or '-'}**")
        signal_cols[2].caption("Score")
        signal_cols[2].write(f"**{row.get('Score probable') or '-'}**")

        st.info(row.get("Résumé") or "Résumé non disponible.")

        prediction = _load_cached_prediction(fixture_id)
        if prediction:
            st.markdown("**Conseil API**")
            st.write(prediction.get("api_advice") or prediction.get("api_winner") or "Non disponible")
            st.caption(f"Probabilités API 1/N/2: {_api_probability_line(prediction)}")
        else:
            st.caption("Conseil API non synchronisé.")


def _render_match_cards(round_rows: pd.DataFrame, force_api_refresh: bool = False):
    for start in range(0, len(round_rows), 2):
        cols = st.columns(2)
        for col, (_, row) in zip(cols, round_rows.iloc[start:start + 2].iterrows()):
            with col:
                _render_match_card(row, force_api_refresh=force_api_refresh)


def show():
    schema_guard.ensure_fixture_api_cache_tables()
    ui.page_hero(
        "Matchs à venir",
        "Consultez les prochaines affiches importées dans la base, avec dates, heures et résumé prévisionnel pour chaque ligue.",
    )

    leagues = _load_leagues_with_upcoming()
    if leagues.empty:
        st.warning("Aucun match à venir n'est présent dans la base. Lancez une mise à jour des championnats en cours.")
        return

    league_labels = {
        int(row.id): f"{row.name} — {row.country}" if row.country else str(row.name)
        for row in leagues.itertuples()
    }

    ui.section_label("Configuration")
    with st.container(border=True):
        cols = st.columns([1.4, 0.8])
        selected_leagues = cols[0].multiselect(
            "Ligues",
            options=list(league_labels.keys()),
            default=list(league_labels.keys()),
            format_func=lambda league_id: league_labels[league_id],
        )
        days_ahead = cols[1].number_input("Jours à venir", min_value=1, max_value=365, value=365, step=7)
        compare_api = st.checkbox("Comparer avec l'API et mettre à jour", value=False)

    if st.session_state.get("upcoming_sync_message"):
        st.success(st.session_state.pop("upcoming_sync_message"))

    upcoming = _load_upcoming_matches(int(days_ahead), selected_leagues)
    if upcoming.empty:
        st.info("Aucun match à venir ne correspond aux filtres sélectionnés.")
        return

    progress_bar = st.progress(0, text="Préparation du téléchargement...")
    status_slot = st.empty()

    _sync_missing_fixture_details(
        upcoming,
        progress_callback=lambda current, total, label: _update_progress(progress_bar, status_slot, current, total, label),
    )
    upcoming = _enrich_with_api_details(
        upcoming,
        progress_callback=lambda current, total, label: _update_progress(progress_bar, status_slot, current, total, label),
    )

    total_matches = len(upcoming)
    league_count = upcoming["league_id"].nunique()
    first_match = _format_datetime(upcoming["date"].min())
    last_match = _format_datetime(upcoming["date"].max())
    cols = st.columns(4)
    cols[0].metric("Matchs trouvés", total_matches)
    cols[1].metric("Ligues", league_count)
    cols[2].metric("Premier match", first_match)
    cols[3].metric("Dernier match", last_match)

    previews = _build_previews(
        upcoming,
        int(len(upcoming)),
        progress_callback=lambda current, total, label: _update_progress(progress_bar, status_slot, current, total, label),
    )
    progress_bar.progress(1.0, text="Chargement terminé")
    status_slot.caption("Toutes les données demandées sont prêtes.")

    with st.container(border=True):
        st.markdown("### Disponibilité complète")
        st.caption(
            "Ce bouton synchronise SQLite avec l’API pour tous les matchs à venir du filtre courant: "
            "les données identiques restent inchangées, les différences sont mises à jour."
        )
        if st.button("Synchroniser toutes les journées", type="primary", width="stretch"):
            bulk_progress = st.progress(0, text="Synchronisation des détails des matchs...")
            bulk_status = st.empty()
            fixture_stats = _sync_fixture_details(
                upcoming,
                progress_callback=lambda current, total, label: _update_progress(bulk_progress, bulk_status, current, total, label),
            )
            prediction_stats = _prefetch_api_predictions(
                previews["fixture_id"].tolist(),
                force_refresh=bool(compare_api),
                progress_callback=lambda current, total, label: _update_progress(bulk_progress, bulk_status, current, total, label),
            )
            bulk_progress.progress(1.0, text="Toutes les journées sont disponibles")
            sync_message = (
                "Détails matchs: "
                f"{fixture_stats['inserted']} ajout(s), {fixture_stats['updated']} mise(s) à jour, "
                f"{fixture_stats['unchanged']} inchangé(s). "
                "Conseils API: "
                f"{prediction_stats['inserted']} ajout(s), {prediction_stats['updated']} mise(s) à jour, "
                f"{prediction_stats['unchanged']} inchangé(s), {prediction_stats['unavailable']} indisponible(s)."
            )
            bulk_status.caption(sync_message)
            st.session_state["upcoming_sync_message"] = f"SQLite est synchronisée avec les données API disponibles. {sync_message}"
            st.rerun()

    ui.section_label("Journées")
    league_options = previews["Championnat"].drop_duplicates().tolist()
    selected_league = st.selectbox("Ligue", options=league_options, key="upcoming_league")
    league_rows = previews[previews["Championnat"] == selected_league].copy()
    round_options = sorted(
        league_rows["Journée"].dropna().drop_duplicates().tolist(),
        key=_round_sort_key,
    )
    selected_round = st.selectbox("Journée", options=round_options, key="upcoming_round")
    round_rows = league_rows[league_rows["Journée"] == selected_round].copy().sort_values(["Date", "Heure", "Match"])

    api_progress = st.progress(0, text="Vérification des conseils API...")
    api_status = st.empty()
    api_stats = _prefetch_api_predictions(
        round_rows["fixture_id"].tolist(),
        force_refresh=bool(compare_api),
        progress_callback=lambda current, total, label: _update_progress(api_progress, api_status, current, total, label),
    )
    api_progress.progress(1.0, text="Conseils API vérifiés")
    api_status.caption(
        "Conseils API: "
        f"{api_stats['inserted']} ajouté(s), {api_stats['updated']} mis à jour, "
        f"{api_stats['unchanged']} déjà en base, {api_stats['unavailable']} indisponible(s)."
    )

    st.caption(f"{selected_league} - {selected_round} - {len(round_rows)} match(s)")
    _render_match_cards(round_rows, force_api_refresh=False)

    st.caption(
        "Les horaires sont affichés en UTC, comme les dates stockées depuis l’API. "
        "Les résumés sont calculés uniquement à partir des matchs déjà importés dans SQLite."
    )


if __name__ == "__main__":
    ui.run_direct_page("Matchs à venir", show)
