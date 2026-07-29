import math

import pandas as pd
from sqlalchemy import text

from database.database import engine


def _clip(value, minimum=0.0, maximum=100.0) -> float:
    try:
        return max(minimum, min(maximum, float(value)))
    except (TypeError, ValueError):
        return minimum


def _number(value, fallback=0.0) -> float:
    try:
        if pd.isna(value):
            return fallback
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _team_match_score(row: pd.Series, team_id: int) -> dict:
    home = int(row["home_team_id"]) == int(team_id)
    goals_for = _number(row["home_goals"] if home else row["away_goals"])
    goals_against = _number(row["away_goals"] if home else row["home_goals"])
    if goals_for > goals_against:
        result, result_score = "V", 100.0
    elif goals_for == goals_against:
        result, result_score = "N", 45.0
    else:
        result, result_score = "D", 0.0
    attack_score = _clip(goals_for / 3 * 100)
    defense_score = _clip(100 - goals_against / 3 * 100)
    score = 0.60 * result_score + 0.22 * attack_score + 0.18 * defense_score
    opponent = row.get("away_name") if home else row.get("home_name")
    return {
        "date": row.get("date"),
        "season": int(row.get("season")),
        "fixture_id": int(row.get("fixture_id")),
        "label": f"{result} {int(goals_for)}-{int(goals_against)}",
        "opponent": str(opponent or "Adversaire"),
        "score": round(score, 1),
        "goals_for": int(goals_for),
        "goals_against": int(goals_against),
    }


def _player_match_score(row: pd.Series) -> dict:
    minutes = max(0.0, _number(row.get("minutes")))
    rating = _number(row.get("rating"), float("nan"))
    goals = _number(row.get("goals_total"))
    assists = _number(row.get("goals_assists"))
    passes_accuracy = _number(row.get("passes_accuracy"), float("nan"))
    duels = _number(row.get("duels_total"))
    duels_won = _number(row.get("duels_won"))

    signals = []
    if math.isfinite(rating) and rating > 0:
        signals.append((_clip((rating - 5.0) / 3.0 * 100), 0.62))
    if minutes > 0:
        impact_per_90 = (goals + assists * 0.7) * 90 / minutes
        signals.append((_clip(impact_per_90 * 80), 0.18))
    if math.isfinite(passes_accuracy) and passes_accuracy > 0:
        signals.append((_clip(passes_accuracy), 0.12))
    if duels > 0:
        signals.append((_clip(duels_won / duels * 100), 0.08))
    if not signals:
        # A player listed on the bench without minutes is an availability
        # signal, not an average performance. Keep it visible but neutral/
        # slightly below neutral instead of treating it like a played match.
        score = 35.0 if minutes <= 0 else 50.0
    else:
        weight = sum(item[1] for item in signals)
        score = sum(value * item_weight for value, item_weight in signals) / weight

    team_name = row.get("team_name") or "Équipe"
    return {
        "date": row.get("date"),
        "season": int(row.get("season")),
        "fixture_id": int(row.get("fixture_id")),
        "label": f"{_number(row.get('rating'), 0):.1f} · {int(minutes)} min",
        "opponent": str(row.get("opponent_name") or team_name),
        "team_name": str(team_name),
        "score": round(_clip(score), 1),
        "rating": round(rating, 2) if math.isfinite(rating) else None,
        "minutes": int(minutes),
        "appearance": "banc" if minutes <= 0 else "terrain",
        "goals": int(goals),
        "assists": int(assists),
    }


def build_trend(history: pd.DataFrame, score_builder, subject_id=None) -> dict:
    if history.empty:
        return {
            "status": "indisponible",
            "label": "Données insuffisantes",
            "delta": 0.0,
            "recent_average": None,
            "previous_average": None,
            "confidence": 0,
            "history": [],
            "season_transition": False,
        }

    ordered = history.copy()
    ordered["date"] = pd.to_datetime(ordered["date"], errors="coerce")
    ordered = ordered.dropna(subset=["date"]).sort_values("date").tail(10)
    points = [
        score_builder(row, subject_id) if subject_id is not None else score_builder(row)
        for _, row in ordered.iterrows()
    ]
    count = len(points)
    if count < 2:
        return {
            "status": "indisponible",
            "label": "Données insuffisantes",
            "delta": 0.0,
            "recent_average": points[0]["score"] if points else None,
            "previous_average": None,
            "confidence": round(min(100.0, count * 10.0) * min(1.0, count / 5.0)),
            "history": points,
            "season_transition": False,
        }

    # Split into two non-overlapping periods.  With an odd number of matches,
    # the most recent period receives the extra match (no match is counted
    # twice, which previously made the delta artificially small).
    previous_size = max(1, count // 2)
    previous = points[:previous_size]
    recent = points[previous_size:]
    previous_average = sum(point["score"] for point in previous) / len(previous)
    recent_average = sum(point["score"] for point in recent) / len(recent)
    delta = recent_average - previous_average
    if delta >= 4:
        status, label = "progression", "En progression"
    elif delta <= -4:
        status, label = "regression", "En régression"
    else:
        status, label = "stable", "Tendance stable"

    return {
        "status": status,
        "label": label,
        "delta": round(delta, 1),
        "recent_average": round(recent_average, 1),
        "previous_average": round(previous_average, 1),
        # A short history must not look as reliable as a full ten-match
        # sample.  The second factor penalises samples smaller than five.
        "confidence": round(min(100.0, count * 10.0) * min(1.0, count / 5.0)),
        "history": points,
        "match_count": count,
        "season_transition": len({point["season"] for point in points}) > 1,
        "seasons": sorted({point["season"] for point in points}),
    }


def load_team_history(team_id: int, league_id: int | None = None, limit: int = 10) -> pd.DataFrame:
    league_filter = "AND m.league_id = :league_id" if league_id is not None else ""
    params = {"team_id": int(team_id), "limit": int(limit)}
    if league_id is not None:
        params["league_id"] = int(league_id)
    try:
        return pd.read_sql(
            text(
                f"""
                SELECT m.fixture_id, m.season, m.date,
                       m.home_team_id, m.away_team_id,
                       m.home_goals, m.away_goals,
                       home.name AS home_name, away.name AS away_name
                FROM matches m
                LEFT JOIN teams home ON home.id = m.home_team_id
                LEFT JOIN teams away ON away.id = m.away_team_id
                WHERE (m.home_team_id = :team_id OR m.away_team_id = :team_id)
                  AND m.home_goals IS NOT NULL
                  AND m.away_goals IS NOT NULL
                  {league_filter}
                ORDER BY m.date DESC
                LIMIT :limit
                """
            ),
            engine,
            params=params,
        )
    except Exception:
        return pd.DataFrame()


def team_progression(team_id: int, league_id: int | None = None, limit: int = 10) -> dict:
    history = load_team_history(team_id, league_id=league_id, limit=limit)
    return build_trend(history, _team_match_score, subject_id=int(team_id))


def load_player_history(player_id: int, league_id: int | None = None, limit: int = 10) -> pd.DataFrame:
    league_filter = "AND m.league_id = :league_id" if league_id is not None else ""
    params = {"player_id": int(player_id), "limit": int(limit)}
    if league_id is not None:
        params["league_id"] = int(league_id)
    try:
        return pd.read_sql(
            text(
                f"""
                SELECT fps.*, m.date, m.season,
                       t.name AS team_name,
                       CASE
                           WHEN m.home_team_id = fps.team_id THEN away.name
                           ELSE home.name
                       END AS opponent_name
                FROM fixture_player_statistics fps
                JOIN matches m ON m.fixture_id = fps.fixture_id
                LEFT JOIN teams t ON t.id = fps.team_id
                LEFT JOIN teams home ON home.id = m.home_team_id
                LEFT JOIN teams away ON away.id = m.away_team_id
                WHERE fps.player_id = :player_id
                  -- Keep bench appearances too: a zero-minute substitute is
                  -- useful context for availability and must not disappear
                  -- from the player's recent history.
                  {league_filter}
                ORDER BY m.date DESC
                LIMIT :limit
                """
            ),
            engine,
            params=params,
        )
    except Exception:
        return pd.DataFrame()


def player_progression(player_id: int, league_id: int | None = None, limit: int = 10) -> dict:
    history = load_player_history(player_id, league_id=league_id, limit=limit)
    return build_trend(history, _player_match_score)
