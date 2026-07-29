import datetime
import json
import time
from typing import Any

import pandas as pd
from sqlalchemy import text

from database import models
from database.database import SessionLocal, engine
from services.api_football import ApiFootballClient


client = ApiFootballClient()


def _utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def _as_int(value):
    if value is None or value == "":
        return None
    try:
        return int(float(str(value).replace("%", "").strip()))
    except (TypeError, ValueError):
        return None


def _as_float(value):
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _api_error(response: dict) -> str | None:
    errors = response.get("errors")
    if not errors:
        return None
    if isinstance(errors, dict):
        return " ; ".join(f"{key}: {value}" for key, value in errors.items())
    if isinstance(errors, list):
        return " ; ".join(str(value) for value in errors)
    return str(errors)


def _upsert_player(session, payload: dict, now: datetime.datetime):
    player_data = payload.get("player") or {}
    player_id = _as_int(player_data.get("id"))
    if player_id is None:
        return 0

    birth = player_data.get("birth") or {}
    player = session.get(models.Player, player_id) or models.Player(id=player_id)
    player.name = player_data.get("name") or f"Joueur {player_id}"
    player.firstname = player_data.get("firstname")
    player.lastname = player_data.get("lastname")
    player.age = _as_int(player_data.get("age"))
    player.birth_date = birth.get("date")
    player.birth_place = birth.get("place")
    player.birth_country = birth.get("country")
    player.nationality = player_data.get("nationality")
    player.height = player_data.get("height")
    player.weight = player_data.get("weight")
    player.injured = player_data.get("injured")
    player.photo = player_data.get("photo")
    player.raw_json = _json(player_data)
    player.updated_at = now
    session.add(player)

    saved = 0
    for statistic_data in payload.get("statistics") or []:
        league = statistic_data.get("league") or {}
        team = statistic_data.get("team") or {}
        league_id = _as_int(league.get("id"))
        season = _as_int(league.get("season"))
        team_id = _as_int(team.get("id"))
        if league_id is None or season is None or team_id is None:
            continue

        key = {
            "player_id": player_id,
            "league_id": league_id,
            "season": season,
            "team_id": team_id,
        }
        row = session.get(models.PlayerStatistic, tuple(key.values()))
        if row is None:
            row = models.PlayerStatistic(**key)

        games = statistic_data.get("games") or {}
        substitutes = statistic_data.get("substitutes") or {}
        shots = statistic_data.get("shots") or {}
        goals = statistic_data.get("goals") or {}
        passes = statistic_data.get("passes") or {}
        tackles = statistic_data.get("tackles") or {}
        duels = statistic_data.get("duels") or {}
        dribbles = statistic_data.get("dribbles") or {}
        fouls = statistic_data.get("fouls") or {}
        cards = statistic_data.get("cards") or {}
        penalty = statistic_data.get("penalty") or {}

        values = {
            "team_name": team.get("name"),
            "team_logo": team.get("logo"),
            "league_name": league.get("name"),
            "league_country": league.get("country"),
            "league_logo": league.get("logo"),
            "league_flag": league.get("flag"),
            "games_appearences": _as_int(games.get("appearences")),
            "games_lineups": _as_int(games.get("lineups")),
            "games_minutes": _as_int(games.get("minutes")),
            "games_number": _as_int(games.get("number")),
            "games_position": games.get("position"),
            "games_rating": _as_float(games.get("rating")),
            "games_captain": games.get("captain"),
            "substitutes_in": _as_int(substitutes.get("in")),
            "substitutes_out": _as_int(substitutes.get("out")),
            "substitutes_bench": _as_int(substitutes.get("bench")),
            "shots_total": _as_int(shots.get("total")),
            "shots_on": _as_int(shots.get("on")),
            "goals_total": _as_int(goals.get("total")),
            "goals_conceded": _as_int(goals.get("conceded")),
            "goals_assists": _as_int(goals.get("assists")),
            "goals_saves": _as_int(goals.get("saves")),
            "passes_total": _as_int(passes.get("total")),
            "passes_key": _as_int(passes.get("key")),
            "passes_accuracy": _as_int(passes.get("accuracy")),
            "tackles_total": _as_int(tackles.get("total")),
            "tackles_blocks": _as_int(tackles.get("blocks")),
            "tackles_interceptions": _as_int(tackles.get("interceptions")),
            "duels_total": _as_int(duels.get("total")),
            "duels_won": _as_int(duels.get("won")),
            "dribbles_attempts": _as_int(dribbles.get("attempts")),
            "dribbles_success": _as_int(dribbles.get("success")),
            "dribbles_past": _as_int(dribbles.get("past")),
            "fouls_drawn": _as_int(fouls.get("drawn")),
            "fouls_committed": _as_int(fouls.get("committed")),
            "cards_yellow": _as_int(cards.get("yellow")),
            "cards_yellowred": _as_int(cards.get("yellowred")),
            "cards_red": _as_int(cards.get("red")),
            "penalty_won": _as_int(penalty.get("won")),
            "penalty_committed": _as_int(penalty.get("commited")),
            "penalty_scored": _as_int(penalty.get("scored")),
            "penalty_missed": _as_int(penalty.get("missed")),
            "penalty_saved": _as_int(penalty.get("saved")),
            "raw_json": _json(statistic_data),
            "updated_at": now,
        }
        for field, value in values.items():
            setattr(row, field, value)
        session.add(row)
        saved += 1
    return saved


def sync_players(league_id: int, season: int, team_id: int | None = None, max_pages: int = 100) -> dict:
    """Synchronise tous les profils paginés pour une ligue/saison ou une équipe."""
    models.Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    pages = 0
    profiles = 0
    statistics = 0
    total_pages = 1
    try:
        page = 1
        while page <= total_pages and page <= max_pages:
            response = client.get_players(
                league_id=int(league_id),
                season=int(season),
                team_id=int(team_id) if team_id is not None else None,
                page=page,
            )
            error = _api_error(response)
            if error:
                raise RuntimeError(error)

            items = response.get("response") or []
            now = _utc_now()
            for payload in items:
                statistics += _upsert_player(session, payload, now)
                profiles += 1
            session.commit()

            pages += 1
            paging = response.get("paging") or {}
            total_pages = max(1, _as_int(paging.get("total")) or 1)
            page += 1
            if page <= total_pages:
                time.sleep(0.35)

        truncated = total_pages > max_pages
        return {
            "profiles": profiles,
            "statistics": statistics,
            "pages": pages,
            "total_pages": total_pages,
            "truncated": truncated,
        }
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def load_scopes() -> pd.DataFrame:
    query = """
        SELECT DISTINCT ls.league_id, COALESCE(l.name, 'Ligue ' || ls.league_id) AS league_name, ls.season
        FROM league_seasons ls
        LEFT JOIN leagues l ON l.id = ls.league_id
        UNION
        SELECT DISTINCT m.league_id, COALESCE(l.name, 'Ligue ' || m.league_id) AS league_name, m.season
        FROM matches m
        LEFT JOIN leagues l ON l.id = m.league_id
        ORDER BY league_name, season DESC
    """
    try:
        return pd.read_sql(query, engine)
    except Exception:
        return pd.DataFrame(columns=["league_id", "league_name", "season"])


def recommended_player_season(league_id: int, seasons) -> int | None:
    available = sorted({int(value) for value in seasons}, reverse=True)
    if not available:
        return None
    placeholders = ",".join(
        f":season_{index}" for index in range(len(available))
    )
    params = {"league_id": int(league_id)}
    params.update(
        {f"season_{index}": season for index, season in enumerate(available)}
    )
    try:
        with engine.begin() as conn:
            stored = conn.execute(
                text(
                    f"""
                    SELECT MAX(season)
                    FROM player_statistics
                    WHERE league_id = :league_id
                      AND season IN ({placeholders})
                    """
                ),
                params,
            ).scalar_one_or_none()
            if stored is not None:
                return int(stored)
            completed = conn.execute(
                text(
                    f"""
                    SELECT MAX(season)
                    FROM matches
                    WHERE league_id = :league_id
                      AND season IN ({placeholders})
                      AND home_goals IS NOT NULL
                      AND away_goals IS NOT NULL
                    """
                ),
                params,
            ).scalar_one_or_none()
            if completed is not None:
                return int(completed)
    except Exception:
        pass
    return available[0]


def load_teams(league_id: int, season: int) -> pd.DataFrame:
    query = text(
        """
        SELECT team_id, MAX(team_name) AS team_name, MAX(logo) AS logo
        FROM (
            SELECT t.id AS team_id, t.name AS team_name, t.logo
            FROM matches m
            JOIN teams t ON t.id = m.home_team_id
            WHERE m.league_id = :league_id AND m.season = :season
            UNION
            SELECT t.id AS team_id, t.name AS team_name, t.logo
            FROM matches m
            JOIN teams t ON t.id = m.away_team_id
            WHERE m.league_id = :league_id AND m.season = :season
            UNION
            SELECT team_id, team_name, team_logo AS logo
            FROM player_statistics
            WHERE league_id = :league_id AND season = :season
        )
        GROUP BY team_id
        ORDER BY team_name
        """
    )
    try:
        return pd.read_sql(query, engine, params={"league_id": int(league_id), "season": int(season)})
    except Exception:
        return pd.DataFrame(columns=["team_id", "team_name", "logo"])


def load_players(league_id: int, season: int, team_id: int | None = None) -> pd.DataFrame:
    where_team = "AND ps.team_id = :team_id" if team_id is not None else ""
    query = text(
        f"""
        SELECT
            p.id, p.name, p.firstname, p.lastname, p.age,
            p.birth_date, p.birth_place, p.birth_country, p.nationality,
            p.height, p.weight, p.injured, p.photo,
            p.raw_json AS profile_raw_json,
            p.updated_at AS profile_updated_at,
            ps.*
        FROM player_statistics ps
        JOIN players p ON p.id = ps.player_id
        WHERE ps.league_id = :league_id AND ps.season = :season
        {where_team}
        ORDER BY p.name, ps.team_name
        """
    )
    params = {"league_id": int(league_id), "season": int(season)}
    if team_id is not None:
        params["team_id"] = int(team_id)
    try:
        frame = pd.read_sql(query, engine, params=params)
        return frame.rename(
            columns={
                "raw_json": "statistics_raw_json",
                "updated_at": "statistics_updated_at",
            }
        )
    except Exception:
        return pd.DataFrame()


def load_player_history(player_id: int) -> pd.DataFrame:
    query = text(
        """
        SELECT
            ps.season, ps.league_name, ps.team_name, ps.games_position,
            ps.games_appearences, ps.games_lineups, ps.games_minutes,
            ps.games_rating, ps.goals_total, ps.goals_assists,
            ps.cards_yellow, ps.cards_red
        FROM player_statistics ps
        WHERE ps.player_id = :player_id
        ORDER BY ps.season DESC, ps.league_name, ps.team_name
        """
    )
    try:
        return pd.read_sql(query, engine, params={"player_id": int(player_id)})
    except Exception:
        return pd.DataFrame()
