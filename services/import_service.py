import logging
from typing import List
from .api_football import ApiFootballClient
from database.database import engine, SessionLocal
from database import models
import datetime
import json
import os
import time
import requests
from sqlalchemy import text
from requests.exceptions import HTTPError
from services.season_format import season_range

logger = logging.getLogger(__name__)
client = ApiFootballClient()
DEFAULT_LEAGUE_IDS = [61, 39, 140, 135, 78, 2]
DEFAULT_START_SEASON = 2016
DEFAULT_END_SEASON = 2026

# configure basic logging if not set
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO)


def init_db():
    """Create database tables."""
    models.Base.metadata.create_all(bind=engine)
    _ensure_schema_columns()
    _ensure_sync_state_table()
    _ensure_connection_log_table()
    _ensure_update_log_table()
    _ensure_league_seasons_table()
    config = get_auto_refresh_config()
    register_league_seasons(
        config["league_ids"],
        range(config["start_season"], config["end_season"] + 1),
        source="configuration",
    )


def _ensure_schema_columns():
    with engine.begin() as conn:
        standing_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(standings)")).fetchall()}
        if "league_id" not in standing_columns:
            conn.execute(text("ALTER TABLE standings ADD COLUMN league_id INTEGER"))
        conn.execute(
            text(
                """
                UPDATE standings
                SET league_id = (
                    SELECT league_id FROM teams WHERE teams.id = standings.team_id
                )
                WHERE league_id IS NULL
                """
            )
        )


def _ensure_sync_state_table():
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS sync_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
        )


def _ensure_connection_log_table():
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS connection_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    connected_at TEXT NOT NULL,
                    refreshed_current INTEGER NOT NULL DEFAULT 0
                )
                """
            )
        )


def _ensure_update_log_table():
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS update_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL,
                    duration_seconds REAL,
                    reason TEXT,
                    leagues TEXT,
                    seasons TEXT,
                    forced_seasons TEXT,
                    details TEXT,
                    error TEXT
                )
                """
            )
        )


def _ensure_league_seasons_table():
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS league_seasons (
                    league_id INTEGER NOT NULL,
                    season INTEGER NOT NULL,
                    source TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (league_id, season)
                )
                """
            )
        )


def register_league_seasons(league_ids, seasons, source: str = "import"):
    _ensure_league_seasons_table()
    now = datetime.datetime.utcnow().isoformat()
    rows = [
        {"league_id": int(league_id), "season": int(season), "source": source, "updated_at": now}
        for league_id in league_ids
        for season in seasons
    ]
    if not rows:
        return
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO league_seasons (league_id, season, source, updated_at)
                VALUES (:league_id, :season, :source, :updated_at)
                ON CONFLICT(league_id, season) DO UPDATE SET
                    source = excluded.source,
                    updated_at = excluded.updated_at
                """
            ),
            rows,
        )


def _json_dump(value) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)


def record_update_log(
    event_type: str,
    status: str,
    started_at: str | None = None,
    finished_at: str | None = None,
    reason: str | None = None,
    leagues=None,
    seasons=None,
    forced_seasons=None,
    details=None,
    error: str | None = None,
) -> int:
    _ensure_update_log_table()
    finished_at = finished_at or datetime.datetime.utcnow().isoformat()
    started_at = started_at or finished_at
    duration_seconds = None
    try:
        duration_seconds = round(
            (datetime.datetime.fromisoformat(finished_at) - datetime.datetime.fromisoformat(started_at)).total_seconds(),
            2,
        )
    except Exception:
        pass
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                INSERT INTO update_log (
                    event_type, status, started_at, finished_at, duration_seconds,
                    reason, leagues, seasons, forced_seasons, details, error
                )
                VALUES (
                    :event_type, :status, :started_at, :finished_at, :duration_seconds,
                    :reason, :leagues, :seasons, :forced_seasons, :details, :error
                )
                """
            ),
            {
                "event_type": event_type,
                "status": status,
                "started_at": started_at,
                "finished_at": finished_at,
                "duration_seconds": duration_seconds,
                "reason": reason,
                "leagues": _json_dump(leagues),
                "seasons": _json_dump(seasons),
                "forced_seasons": _json_dump(forced_seasons),
                "details": _json_dump(details),
                "error": error,
            },
        )
        return int(result.lastrowid)


def record_update_result(event_type: str, started_at: str, result: dict, error: str | None = None) -> int:
    status = "erreur" if error else "effectuée" if result.get("ran") else "ignorée"
    config = result.get("config") or {}
    leagues = result.get("refreshed") or config.get("league_ids")
    return record_update_log(
        event_type=event_type,
        status=status,
        started_at=started_at,
        finished_at=result.get("refreshed_at") or datetime.datetime.utcnow().isoformat(),
        reason=result.get("reason"),
        leagues=leagues,
        seasons=result.get("seasons"),
        forced_seasons=result.get("force_refresh_seasons"),
        details=result,
        error=error,
    )


def _get_sync_value(key: str):
    _ensure_sync_state_table()
    with engine.begin() as conn:
        row = conn.execute(text("SELECT value FROM sync_state WHERE key = :key"), {"key": key}).fetchone()
    return row[0] if row else None


def _set_sync_value(key: str, value: str):
    _ensure_sync_state_table()
    now = datetime.datetime.utcnow().isoformat()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO sync_state (key, value, updated_at)
                VALUES (:key, :value, :updated_at)
                ON CONFLICT(key) DO UPDATE SET value = :value, updated_at = :updated_at
                """
            ),
            {"key": key, "value": value, "updated_at": now},
        )


def record_connection(connected_at: str) -> int:
    _ensure_connection_log_table()
    with engine.begin() as conn:
        result = conn.execute(
            text("INSERT INTO connection_log (connected_at, refreshed_current) VALUES (:connected_at, 0)"),
            {"connected_at": connected_at},
        )
        return int(result.lastrowid)


def mark_connection_current_refreshed(connection_id: int):
    _ensure_connection_log_table()
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE connection_log SET refreshed_current = 1 WHERE id = :connection_id"),
            {"connection_id": connection_id},
        )


def format_connection_label(raw: str) -> str:
    try:
        dt = datetime.datetime.fromisoformat(raw)
        return dt.strftime("%d/%m/%Y %H:%M:%S UTC")
    except Exception:
        return raw


def _parse_int_list(value: str, fallback: list[int]) -> list[int]:
    if not value:
        return fallback
    try:
        parsed = [int(item.strip()) for item in value.split(",") if item.strip()]
        return parsed or fallback
    except Exception:
        return fallback


def get_auto_refresh_config() -> dict:
    configured_end_season = os.getenv("AUTO_REFRESH_END_SEASON", "").strip()
    if configured_end_season:
        end_season = max(int(configured_end_season), DEFAULT_END_SEASON)
    else:
        current_year = datetime.datetime.utcnow().year
        try:
            with engine.begin() as conn:
                stored_end_season = conn.execute(
                    text(
                        """
                        SELECT MAX(season) FROM (
                            SELECT season FROM league_seasons
                            UNION
                            SELECT season FROM matches
                        )
                        """
                    )
                ).scalar_one_or_none()
            stored_end_season = int(stored_end_season) if stored_end_season else DEFAULT_END_SEASON
            end_season = max(current_year, stored_end_season)
        except Exception:
            end_season = max(current_year, DEFAULT_END_SEASON)
    return {
        "enabled": os.getenv("AUTO_REFRESH_ON_CONNECTION", "true").lower() in {"1", "true", "yes", "oui"},
        "current_enabled": os.getenv("AUTO_REFRESH_CURRENT_ON_CONNECTION", "true").lower() in {"1", "true", "yes", "oui"},
        "league_ids": _parse_int_list(os.getenv("AUTO_REFRESH_LEAGUE_IDS", ""), DEFAULT_LEAGUE_IDS),
        "start_season": int(os.getenv("AUTO_REFRESH_START_SEASON", str(DEFAULT_START_SEASON))),
        "end_season": end_season,
        "recent_seasons": int(os.getenv("AUTO_REFRESH_RECENT_SEASONS", "2")),
        "interval_minutes": int(os.getenv("AUTO_REFRESH_INTERVAL_MINUTES", "360")),
        "pause": float(os.getenv("AUTO_REFRESH_PAUSE_SECONDS", "0.8")),
        "max_retries": int(os.getenv("AUTO_REFRESH_MAX_RETRIES", "3")),
    }


def _last_refresh_is_recent(interval_minutes: int) -> bool:
    raw = _get_sync_value("last_auto_refresh_utc")
    if not raw:
        return False
    try:
        last = datetime.datetime.fromisoformat(raw)
    except Exception:
        return False
    age = datetime.datetime.utcnow() - last
    return age.total_seconds() < interval_minutes * 60


def audit_configured_season_access(config: dict | None = None) -> dict:
    config = config or get_auto_refresh_config()
    seasons = list(range(config["start_season"], config["end_season"] + 1))
    sample_league = config["league_ids"][0]
    accessible = []
    unavailable = []
    for season in seasons:
        try:
            resp = client.get_fixtures(sample_league, season)
            errors = resp.get("errors") or {}
            if errors:
                unavailable.append({"season": season, "reason": str(errors)})
            else:
                accessible.append(season)
        except Exception as exc:
            unavailable.append({"season": season, "reason": str(exc)})
    summary = {"sample_league": sample_league, "accessible": accessible, "unavailable": unavailable}
    _set_sync_value("last_api_access_audit", str(summary))
    return summary


def _get_or_create_league(session, league_info: dict):
    lid = int(league_info.get("id") or league_info.get("league", {}).get("id"))
    country_info = league_info.get("country")
    if isinstance(country_info, dict):
        country_value = country_info.get("name")
    else:
        country_value = country_info
    league = session.get(models.League, lid)
    if league:
        league.name = league_info.get("name") or league_info.get("league", {}).get("name") or league.name
        league.country = country_value or league.country
        league.logo = league_info.get("logo") or league_info.get("league", {}).get("logo") or league.logo
        session.add(league)
        session.commit()
        return league
    name = league_info.get("name") or league_info.get("league", {}).get("name")
    logo = league_info.get("logo") or league_info.get("league", {}).get("logo")
    league = models.League(id=lid, name=name, country=country_value, logo=logo)
    session.add(league)
    session.commit()
    return league


def _sync_league_metadata(session, league_id: int, season: int | None = None):
    try:
        params = {"id": league_id}
        if season is not None:
            params["season"] = season
        resp = client._get("/leagues", params)
        items = resp.get("response", [])
        if items:
            return _get_or_create_league(session, items[0])
    except Exception as exc:
        logger.warning(f"Failed to fetch league metadata for {league_id}: {exc}")
    return None


def _get_or_create_team(session, team_info: dict, league_id: int = None):
    tid = int(team_info.get("id") or team_info.get("team", {}).get("id"))
    team = session.get(models.Team, tid)
    if team:
        team.name = team_info.get("name") or team_info.get("team", {}).get("name") or team.name
        team.logo = team_info.get("logo") or team_info.get("team", {}).get("logo") or team.logo
        team.country = team_info.get("country") or team_info.get("team", {}).get("country") or team.country
        team.league_id = team.league_id or league_id
        session.add(team)
        session.commit()
        return team
    name = team_info.get("name") or team_info.get("team", {}).get("name")
    logo = team_info.get("logo") or team_info.get("team", {}).get("logo")
    country = team_info.get("country") or team_info.get("team", {}).get("country")
    team = models.Team(id=tid, league_id=league_id, name=name, logo=logo, country=country)
    session.add(team)
    session.commit()
    return team


def _save_match(session, fixture: dict, league_id: int, season: int):
    fixture_id = int(fixture.get("id") or fixture.get("fixture", {}).get("id"))
    existing = session.get(models.Match, fixture_id)
    # Build normalized fields
    start = fixture.get("date") or fixture.get("fixture", {}).get("date")
    try:
        date = datetime.datetime.fromisoformat(start.replace('Z', '+00:00')) if start else None
    except Exception:
        date = None
    home = fixture.get("homeTeam") or fixture.get("teams", {}).get("home") or fixture.get("teams")
    away = fixture.get("awayTeam") or fixture.get("teams", {}).get("away") if fixture.get("teams") else None
    # API v3 sometimes nests team info under 'teams' with 'home'/'away' keys
    if isinstance(home, dict) and 'id' in home:
        home_id = int(home.get('id') or home.get('team', {}).get('id'))
    else:
        home_id = None
    if isinstance(away, dict) and 'id' in away:
        away_id = int(away.get('id') or away.get('team', {}).get('id'))
    else:
        away_id = None
    goals = fixture.get("goals") or fixture.get("score") or {}
    home_goals = goals.get("home") if isinstance(goals, dict) else None
    away_goals = goals.get("away") if isinstance(goals, dict) else None
    status_data = fixture.get("status") or fixture.get("fixture", {}).get("status")
    status = status_data.get("long") if isinstance(status_data, dict) else status_data
    winner = None
    if home_goals is not None and away_goals is not None:
        if home_goals > away_goals:
            winner = 'home'
        elif home_goals < away_goals:
            winner = 'away'
        else:
            winner = 'draw'

    if existing:
        # update fields
        existing.league_id = league_id
        existing.season = season
        existing.date = date
        existing.home_team_id = home_id or existing.home_team_id
        existing.away_team_id = away_id or existing.away_team_id
        existing.home_goals = home_goals
        existing.away_goals = away_goals
        existing.winner = winner
        existing.status = status
        session.add(existing)
    else:
        match = models.Match(fixture_id=fixture_id, league_id=league_id, season=season, date=date,
                             home_team_id=home_id or 0, away_team_id=away_id or 0,
                             home_goals=home_goals, away_goals=away_goals, winner=winner, status=status)
        session.add(match)
    session.commit()


def _configured_season_range() -> list[int]:
    config = get_auto_refresh_config()
    return list(range(config["start_season"], config["end_season"] + 1))


def import_league_seasons_by_id(league_id: int, seasons: List[int] | None = None):
    """Import fixtures, teams, standings for given `league_id` across given seasons.

    This function is defensive and will skip items when API response differs.
    """
    seasons = seasons or _configured_season_range()
    register_league_seasons([league_id], seasons, source="import")
    session = SessionLocal()
    try:
        for season in seasons:
            logger.info(f"Importing league {league_id} season {season}")
            _sync_league_metadata(session, league_id, season)
            # Teams
            try:
                teams_resp = client.get_teams(league_id, season)
                for t in teams_resp.get('response', []):
                    # t may contain nested structures
                    team_info = t.get('team') if 'team' in t else t
                    _get_or_create_team(session, team_info, league_id=league_id)
            except Exception as e:
                logger.warning(f"Failed to fetch teams for league {league_id} season {season}: {e}")

            # Fixtures (may be paginated by date ranges); fetch whole season
            try:
                fixtures_resp = client.get_fixtures(league_id, season)
                for item in fixtures_resp.get('response', []):
                    fixture = item.get('fixture') if 'fixture' in item else item
                    # Persist teams referenced in fixture
                    # attempt to create teams if nested
                    teams = item.get('teams') or {}
                    if isinstance(teams, dict):
                        home = teams.get('home')
                        away = teams.get('away')
                        if home:
                            _get_or_create_team(session, home, league_id=league_id)
                        if away:
                            _get_or_create_team(session, away, league_id=league_id)
                    _save_match(session, item, league_id, season)
            except Exception as e:
                logger.warning(f"Failed to fetch fixtures for league {league_id} season {season}: {e}")

            # Standings
            try:
                stand_resp = client.get_standings(league_id, season)
                for s in stand_resp.get('response', []):
                    # s may contain 'league' -> 'standings' nested list
                    table = None
                    if isinstance(s, dict) and 'league' in s and 'standings' in s['league']:
                        lists = s['league']['standings']
                        if lists and isinstance(lists, list):
                            # take first group
                            table = lists[0]
                    elif 'response' in stand_resp:
                        # older format
                        table = stand_resp.get('response')
                    if table:
                        for row in table:
                            team = row.get('team') or row
                            team_id = int(team.get('id')) if team and team.get('id') else None
                            if team_id:
                                existing = session.query(models.Standing).filter_by(league_id=league_id, team_id=team_id, season=season).first()
                                position = row.get('rank') or row.get('position')
                                points = row.get('points')
                                wins = row.get('all', {}).get('win') if row.get('all') else row.get('wins')
                                draws = row.get('all', {}).get('draw') if row.get('all') else row.get('draws')
                                losses = row.get('all', {}).get('lose') if row.get('all') else row.get('losses')
                                gf = row.get('all', {}).get('goals', {}).get('for') if row.get('all') else row.get('goals_for')
                                ga = row.get('all', {}).get('goals', {}).get('against') if row.get('all') else row.get('goals_against')
                                gd = row.get('goalsDiff') or row.get('goal_difference')
                                if existing:
                                    existing.league_id = league_id
                                    existing.position = position
                                    existing.points = points
                                    existing.wins = wins
                                    existing.draws = draws
                                    existing.losses = losses
                                    existing.goals_for = gf
                                    existing.goals_against = ga
                                    existing.goal_difference = gd
                                    session.add(existing)
                                else:
                                    srec = models.Standing(league_id=league_id, team_id=team_id, season=season, position=position, points=points,
                                                           wins=wins, draws=draws, losses=losses, goals_for=gf,
                                                           goals_against=ga, goal_difference=gd)
                                    session.add(srec)
                        session.commit()
            except Exception as e:
                logger.warning(f"Failed to fetch standings for league {league_id} season {season}: {e}")
    finally:
        session.close()


def _active_season_for_league(session, league_id: int, fallback_season: int) -> int:
    season = session.execute(
        text(
            """
            SELECT MAX(season) FROM (
                SELECT season FROM league_seasons WHERE league_id = :league_id
                UNION
                SELECT season FROM matches WHERE league_id = :league_id
            )
            """
        ),
        {"league_id": league_id},
    ).scalar_one_or_none()
    stored_season = int(season) if season is not None else None
    if stored_season is None:
        return fallback_season
    return max(stored_season, fallback_season)


def _refresh_league_season(session, league_id: int, season: int, pause: float, max_retries: int):
    _sync_league_metadata(session, league_id, season)

    tries = 0
    while tries < max_retries:
        try:
            resp = client.get_teams(league_id, season)
            for item in resp.get("response", []):
                team_info = item.get("team") if "team" in item else item
                _get_or_create_team(session, team_info, league_id=league_id)
            break
        except HTTPError as exc:
            tries += 1
            logger.warning(f"HTTP error refreshing teams {league_id}/{season}: {exc} — retry {tries}")
            time.sleep(pause * tries)
        except Exception as exc:
            tries += 1
            logger.warning(f"Error refreshing teams {league_id}/{season}: {exc} — retry {tries}")
            time.sleep(pause * tries)

    time.sleep(pause)

    tries = 0
    while tries < max_retries:
        try:
            resp = client.get_fixtures(league_id, season)
            items = resp.get("response", [])
            paging = resp.get("paging") or {}
            page = 1
            total_pages = paging.get("total") or 1
            while True:
                for item in items:
                    teams = item.get("teams") or {}
                    if isinstance(teams, dict):
                        if teams.get("home"):
                            _get_or_create_team(session, teams["home"], league_id=league_id)
                        if teams.get("away"):
                            _get_or_create_team(session, teams["away"], league_id=league_id)
                    _save_match(session, item, league_id, season)
                if page >= total_pages:
                    break
                page += 1
                resp = client._get("/fixtures", {"league": league_id, "season": season, "page": page})
                items = resp.get("response", [])
            break
        except HTTPError as exc:
            tries += 1
            logger.warning(f"HTTP error refreshing fixtures {league_id}/{season}: {exc} — retry {tries}")
            time.sleep(pause * tries)
        except Exception as exc:
            tries += 1
            logger.warning(f"Error refreshing fixtures {league_id}/{season}: {exc} — retry {tries}")
            time.sleep(pause * tries)

    time.sleep(pause)

    tries = 0
    while tries < max_retries:
        try:
            resp = client.get_standings(league_id, season)
            for item in resp.get("response", []):
                table = None
                if isinstance(item, dict) and "league" in item and "standings" in item["league"]:
                    lists = item["league"]["standings"]
                    if lists and isinstance(lists, list):
                        table = lists[0]
                elif "response" in resp:
                    table = resp.get("response")
                if not table:
                    continue
                for row in table:
                    team = row.get("team") or row
                    team_id = int(team.get("id")) if team and team.get("id") else None
                    if not team_id:
                        continue
                    existing = session.query(models.Standing).filter_by(
                        league_id=league_id,
                        team_id=team_id,
                        season=season,
                    ).first()
                    values = {
                        "league_id": league_id,
                        "position": row.get("rank") or row.get("position"),
                        "points": row.get("points"),
                        "wins": row.get("all", {}).get("win") if row.get("all") else row.get("wins"),
                        "draws": row.get("all", {}).get("draw") if row.get("all") else row.get("draws"),
                        "losses": row.get("all", {}).get("lose") if row.get("all") else row.get("losses"),
                        "goals_for": row.get("all", {}).get("goals", {}).get("for") if row.get("all") else row.get("goals_for"),
                        "goals_against": row.get("all", {}).get("goals", {}).get("against") if row.get("all") else row.get("goals_against"),
                        "goal_difference": row.get("goalsDiff") or row.get("goal_difference"),
                    }
                    if existing:
                        for key, value in values.items():
                            setattr(existing, key, value)
                        session.add(existing)
                    else:
                        session.add(models.Standing(team_id=team_id, season=season, **values))
                session.commit()
            break
        except HTTPError as exc:
            tries += 1
            session.rollback()
            logger.warning(f"HTTP error refreshing standings {league_id}/{season}: {exc} — retry {tries}")
            time.sleep(pause * tries)
        except Exception as exc:
            tries += 1
            session.rollback()
            logger.warning(f"Error refreshing standings {league_id}/{season}: {exc} — retry {tries}")
            time.sleep(pause * tries)


def refresh_current_competitions_on_connection() -> dict:
    """Refresh active seasons for every configured competition once per app session."""
    config = get_auto_refresh_config()
    if not config["enabled"] or not config["current_enabled"]:
        return {"ran": False, "reason": "Mise à jour des championnats en cours désactivée.", "config": config}

    register_league_seasons(config["league_ids"], [config["end_season"]], source="current_refresh")
    session = SessionLocal()
    refreshed = []
    try:
        for league_id in config["league_ids"]:
            season = _active_season_for_league(session, league_id, config["end_season"])
            logger.info(f"Refreshing active competition {league_id} season {season}")
            _refresh_league_season(
                session,
                league_id,
                season,
                pause=config["pause"],
                max_retries=config["max_retries"],
            )
            refreshed.append({"league_id": league_id, "season": season})
            time.sleep(config["pause"])
    finally:
        session.close()

    now = datetime.datetime.utcnow().isoformat()
    _set_sync_value("last_current_refresh_utc", now)
    return {
        "ran": True,
        "reason": "Championnats en cours mis à jour.",
        "config": config,
        "refreshed": refreshed,
        "refreshed_at": now,
    }


def import_multiple_leagues(league_ids: List[int], seasons: List[int] | None = None):
    seasons = seasons or _configured_season_range()
    for lid in league_ids:
        import_league_seasons_by_id(lid, seasons=seasons)


def import_leagues_cautious(
    league_ids: List[int],
    seasons: List[int] | None = None,
    pause: float = 1.5,
    max_retries: int = 5,
    force_refresh_seasons: List[int] | None = None,
):
    """Import several leagues with polite pauses, retries and logging.

    - `pause`: base seconds between API calls
    - `max_retries`: number of retries on 429 or transient errors
    """
    seasons = seasons or _configured_season_range()
    register_league_seasons(league_ids, seasons, source="import")
    session = SessionLocal()
    force_refresh_seasons = set(force_refresh_seasons or [])
    try:
        for lid in league_ids:
            logging.info(f"Starting import for league {lid}")
            for season in seasons:
                logging.info(f"Importing {lid} season {season}")
                _sync_league_metadata(session, lid, season)
                # Check existing
                existing_count = session.query(models.Match).filter_by(league_id=lid, season=season).count()
                if existing_count > 0 and season not in force_refresh_seasons:
                    logging.info(f"Season {season} already has {existing_count} matches — skipping")
                    continue

                # Teams
                tries = 0
                while tries < max_retries:
                    try:
                        resp = client.get_teams(lid, season)
                        for t in resp.get('response', []):
                            team_info = t.get('team') if 'team' in t else t
                            _get_or_create_team(session, team_info, league_id=lid)
                        break
                    except HTTPError as e:
                        tries += 1
                        logging.warning(f"HTTP error fetching teams {lid}/{season}: {e} — retry {tries}")
                        time.sleep(pause * tries)
                    except Exception as e:
                        tries += 1
                        logging.warning(f"Error fetching teams {lid}/{season}: {e} — retry {tries}")
                        time.sleep(pause * tries)

                time.sleep(pause)

                # Fixtures
                tries = 0
                while tries < max_retries:
                    try:
                        resp = client.get_fixtures(lid, season)
                        items = resp.get('response', [])
                        # Support paging if present
                        paging = resp.get('paging') or {}
                        page = 1
                        total_pages = paging.get('total') or 1
                        while True:
                            for item in items:
                                teams = item.get('teams') or {}
                                if isinstance(teams, dict):
                                    if teams.get('home'):
                                        _get_or_create_team(session, teams.get('home'), league_id=lid)
                                    if teams.get('away'):
                                        _get_or_create_team(session, teams.get('away'), league_id=lid)
                                _save_match(session, item, lid, season)
                            if page >= total_pages:
                                break
                            page += 1
                            resp = client._get('/fixtures', {'league': lid, 'season': season, 'page': page})
                            items = resp.get('response', [])
                        break
                    except HTTPError as e:
                        tries += 1
                        logging.warning(f"HTTP error fetching fixtures {lid}/{season}: {e} — retry {tries}")
                        time.sleep(pause * tries)
                    except Exception as e:
                        tries += 1
                        logging.warning(f"Error fetching fixtures {lid}/{season}: {e} — retry {tries}")
                        time.sleep(pause * tries)

                time.sleep(pause)

                # Standings
                tries = 0
                while tries < max_retries:
                    try:
                        resp = client.get_standings(lid, season)
                        for s in resp.get('response', []):
                            table = None
                            if isinstance(s, dict) and 'league' in s and 'standings' in s['league']:
                                lists = s['league']['standings']
                                if lists and isinstance(lists, list):
                                    table = lists[0]
                            elif 'response' in resp:
                                table = resp.get('response')
                            if table:
                                for row in table:
                                    team = row.get('team') or row
                                    team_id = int(team.get('id')) if team and team.get('id') else None
                                    if team_id:
                                        existing = session.query(models.Standing).filter_by(league_id=lid, team_id=team_id, season=season).first()
                                        position = row.get('rank') or row.get('position')
                                        points = row.get('points')
                                        wins = row.get('all', {}).get('win') if row.get('all') else row.get('wins')
                                        draws = row.get('all', {}).get('draw') if row.get('all') else row.get('draws')
                                        losses = row.get('all', {}).get('lose') if row.get('all') else row.get('losses')
                                        gf = row.get('all', {}).get('goals', {}).get('for') if row.get('all') else row.get('goals_for')
                                        ga = row.get('all', {}).get('goals', {}).get('against') if row.get('all') else row.get('goals_against')
                                        gd = row.get('goalsDiff') or row.get('goal_difference')
                                        if existing:
                                            existing.league_id = lid
                                            existing.position = position
                                            existing.points = points
                                            existing.wins = wins
                                            existing.draws = draws
                                            existing.losses = losses
                                            existing.goals_for = gf
                                            existing.goals_against = ga
                                            existing.goal_difference = gd
                                            session.add(existing)
                                        else:
                                            srec = models.Standing(league_id=lid, team_id=team_id, season=season, position=position, points=points,
                                                                   wins=wins, draws=draws, losses=losses, goals_for=gf,
                                                                   goals_against=ga, goal_difference=gd)
                                            session.add(srec)
                                session.commit()
                        break
                    except HTTPError as e:
                        tries += 1
                        logging.warning(f"HTTP error fetching standings {lid}/{season}: {e} — retry {tries}")
                        time.sleep(pause * tries)
                    except Exception as e:
                        tries += 1
                        logging.warning(f"Error fetching standings {lid}/{season}: {e} — retry {tries}")
                        time.sleep(pause * tries)

                # polite pause between seasons
                time.sleep(pause * 2)
    finally:
        session.close()


def auto_refresh_if_due(force: bool = False) -> dict:
    """Refresh imported football data when the configured interval has elapsed.

    The refresh checks every configured season from the start year to the current
    year, but existing historical seasons are skipped. Recent seasons are forced
    because scores and standings can change.
    """
    config = get_auto_refresh_config()
    if not config["enabled"] and not force:
        return {"ran": False, "reason": "Synchronisation automatique désactivée.", "config": config}

    if not force and _last_refresh_is_recent(config["interval_minutes"]):
        return {"ran": False, "reason": "Synchronisation récente, aucun appel API relancé.", "config": config}

    audit = audit_configured_season_access(config)
    seasons = audit["accessible"]
    if not seasons:
        _set_sync_value("last_auto_refresh_utc", datetime.datetime.utcnow().isoformat())
        return {
            "ran": False,
            "reason": "Aucune saison configurée n’est accessible avec le plan API actuel.",
            "config": config,
            "audit": audit,
        }

    force_refresh_seasons = seasons[-config["recent_seasons"] :]

    import_leagues_cautious(
        config["league_ids"],
        seasons=seasons,
        pause=config["pause"],
        max_retries=config["max_retries"],
        force_refresh_seasons=force_refresh_seasons,
    )
    now = datetime.datetime.utcnow().isoformat()
    _set_sync_value("last_auto_refresh_utc", now)
    return {
        "ran": True,
        "reason": "Synchronisation terminée.",
        "config": config,
        "seasons": seasons,
        "force_refresh_seasons": force_refresh_seasons,
        "audit": audit,
        "refreshed_at": now,
    }


def get_last_auto_refresh_label() -> str:
    raw = _get_sync_value("last_auto_refresh_utc")
    if not raw:
        return "Aucune synchronisation automatique enregistrée."
    try:
        dt = datetime.datetime.fromisoformat(raw)
        return dt.strftime("%d/%m/%Y %H:%M UTC")
    except Exception:
        return raw


def get_last_current_refresh_label() -> str:
    raw = _get_sync_value("last_current_refresh_utc")
    if not raw:
        return "Aucune mise à jour des championnats en cours enregistrée."
    try:
        dt = datetime.datetime.fromisoformat(raw)
        return dt.strftime("%d/%m/%Y %H:%M UTC")
    except Exception:
        return raw


def get_api_access_message() -> str:
    raw = _get_sync_value("last_api_access_audit")
    if not raw:
        return "Accès API non vérifié."
    try:
        # Stored as a small Python literal dict containing only ints/strings.
        import ast

        audit = ast.literal_eval(raw)
        accessible = audit.get("accessible", [])
        unavailable = audit.get("unavailable", [])
        if not unavailable:
            return f"Saisons sportives accessibles via l’API: {season_range(accessible)}." if accessible else "Aucune saison sportive accessible détectée."
        if accessible:
            return (
                f"Saisons sportives accessibles via le plan API actuel: {season_range(accessible)}. "
                f"{len(unavailable)} saison(s) sportive(s) configurée(s) sont refusées par l’API."
            )
        return "Le plan API actuel refuse toutes les saisons sportives configurées."
    except Exception:
        return raw
