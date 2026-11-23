import logging
from typing import List
from .api_football import ApiFootballClient
from .api_response_store import archive_response
from database.database import engine, SessionLocal
from database import models
import datetime
import json
import os
import threading
import time
import requests
from sqlalchemy import text, inspect
from requests.exceptions import HTTPError
from services.season_format import season_range

logger = logging.getLogger(__name__)
client = ApiFootballClient(response_archiver=archive_response)
DEFAULT_LEAGUE_IDS = [61, 39, 140, 135, 78, 2]
DEFAULT_START_SEASON = 2016
# Do not pin the application to a historic season.  The API's current
# football season is represented by the current calendar year and can still
# be overridden explicitly with AUTO_REFRESH_END_SEASON.
DEFAULT_END_SEASON = datetime.datetime.now(datetime.UTC).year
_db_initialized = False
_db_init_lock = threading.Lock()


def _api_error(response) -> str | None:
    """Return the API error payload instead of treating it as empty data."""
    if not isinstance(response, dict) or not response.get("errors"):
        return None
    errors = response["errors"]
    if isinstance(errors, dict):
        return " ; ".join(f"{key}: {value}" for key, value in errors.items())
    return str(errors)


def _is_quota_error(error: Exception | str) -> bool:
    value = str(error).casefold()
    return any(
        token in value
        for token in (
            "quota", "rate limit", "request limit", "too many requests", "429",
            "emergency_reserve_protected", "réserve protégée", "appel api différé",
        )
    )

# configure basic logging if not set
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO)


def _auto_id_definition(conn) -> str:
    """Return an auto-increment primary-key definition for the active DB."""
    return "SERIAL PRIMARY KEY" if conn.dialect.name == "postgresql" else "INTEGER PRIMARY KEY AUTOINCREMENT"


def utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def init_db():
    """Create database tables."""
    global _db_initialized
    if _db_initialized:
        return
    with _db_init_lock:
        if _db_initialized:
            return
        models.Base.metadata.create_all(bind=engine)
        _ensure_schema_columns()
        _ensure_sync_state_table()
        _ensure_connection_log_table()
        _ensure_update_log_table()
        _ensure_league_seasons_table()
        _ensure_fixture_api_cache_tables()
        from services import api_response_store, schema_guard, sync_registry
        api_response_store.ensure_table()
        schema_guard.ensure_performance_indexes()
        sync_registry.ensure_table()
        config = get_auto_refresh_config()
        register_league_seasons(
            config["league_ids"],
            range(config["start_season"], config["end_season"] + 1),
            source="configuration",
        )
        _db_initialized = True


def _ensure_schema_columns():
    with engine.begin() as conn:
        inspector = inspect(conn)
        match_columns = {column["name"] for column in inspector.get_columns("matches")} if inspector.has_table("matches") else set()
        missing_match_columns = {
            "home_goals": "INTEGER",
            "away_goals": "INTEGER",
            "winner": "TEXT",
            "status": "TEXT",
        }
        for column_name, column_type in missing_match_columns.items():
            if column_name not in match_columns:
                conn.execute(text(f"ALTER TABLE matches ADD COLUMN {column_name} {column_type}"))

        standing_columns = {column["name"] for column in inspector.get_columns("standings")} if inspector.has_table("standings") else set()
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
        id_definition = _auto_id_definition(conn)
        conn.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS connection_log (
                    id {id_definition},
                    connected_at TEXT NOT NULL,
                    refreshed_current INTEGER NOT NULL DEFAULT 0
                )
                """
            )
        )


def _ensure_update_log_table():
    with engine.begin() as conn:
        id_definition = _auto_id_definition(conn)
        conn.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS update_log (
                    id {id_definition},
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


def _ensure_fixture_api_cache_tables():
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS xg_ingestion_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sync_run_id TEXT NOT NULL,
                    fixture_id INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    requested_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    item_count INTEGER NOT NULL DEFAULT 0,
                    has_xg INTEGER NOT NULL DEFAULT 0,
                    payload_sha256 TEXT,
                    response_json TEXT,
                    error TEXT,
                    FOREIGN KEY(fixture_id) REFERENCES matches(fixture_id)
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS fixture_team_statistics (
                    fixture_id INTEGER NOT NULL,
                    team_id INTEGER NOT NULL,
                    team_name TEXT,
                    is_home INTEGER,
                    expected_goals REAL,
                    goals_prevented REAL,
                    source TEXT NOT NULL DEFAULT 'API-Football',
                    source_endpoint TEXT NOT NULL DEFAULT '/fixtures/statistics',
                    source_field TEXT NOT NULL DEFAULT 'expected_goals',
                    retrieved_at TEXT,
                    payload_sha256 TEXT,
                    ingestion_id INTEGER,
                    raw_json TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (fixture_id, team_id),
                    FOREIGN KEY(fixture_id) REFERENCES matches(fixture_id),
                    FOREIGN KEY(team_id) REFERENCES teams(id),
                    FOREIGN KEY(ingestion_id) REFERENCES xg_ingestion_audit(id)
                )
                """
            )
        )
        columns = {
            column["name"]
            for column in inspect(conn).get_columns("fixture_team_statistics")
        }
        additions = {
            "source": "TEXT NOT NULL DEFAULT 'API-Football'",
            "source_endpoint": "TEXT NOT NULL DEFAULT '/fixtures/statistics'",
            "source_field": "TEXT NOT NULL DEFAULT 'expected_goals'",
            "retrieved_at": "TEXT",
            "payload_sha256": "TEXT",
            "ingestion_id": "INTEGER",
        }
        for name, column_type in additions.items():
            if name not in columns:
                conn.execute(
                    text(
                        f"ALTER TABLE fixture_team_statistics "
                        f"ADD COLUMN {name} {column_type}"
                    )
                )
        conn.execute(
            text(
                """
                CREATE TRIGGER IF NOT EXISTS xg_ingestion_audit_no_update
                BEFORE UPDATE ON xg_ingestion_audit
                BEGIN
                    SELECT RAISE(ABORT, 'xg_ingestion_audit is append-only');
                END
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TRIGGER IF NOT EXISTS xg_ingestion_audit_no_delete
                BEFORE DELETE ON xg_ingestion_audit
                BEGIN
                    SELECT RAISE(ABORT, 'xg_ingestion_audit is append-only');
                END
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TRIGGER IF NOT EXISTS fixture_team_statistics_ingestion_insert
                BEFORE INSERT ON fixture_team_statistics
                WHEN NEW.ingestion_id IS NOT NULL
                 AND NOT EXISTS (SELECT 1 FROM xg_ingestion_audit WHERE id = NEW.ingestion_id)
                BEGIN
                    SELECT RAISE(ABORT, 'unknown xG ingestion_id');
                END
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TRIGGER IF NOT EXISTS fixture_team_statistics_ingestion_update
                BEFORE UPDATE OF ingestion_id ON fixture_team_statistics
                WHEN NEW.ingestion_id IS NOT NULL
                 AND NOT EXISTS (SELECT 1 FROM xg_ingestion_audit WHERE id = NEW.ingestion_id)
                BEGIN
                    SELECT RAISE(ABORT, 'unknown xG ingestion_id');
                END
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS fixture_api_details (
                    fixture_id INTEGER PRIMARY KEY,
                    league_id INTEGER NOT NULL,
                    season INTEGER NOT NULL,
                    round TEXT,
                    venue TEXT,
                    city TEXT,
                    status_short TEXT,
                    home_logo TEXT,
                    away_logo TEXT,
                    league_logo TEXT,
                    raw_json TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS fixture_api_predictions (
                    fixture_id INTEGER PRIMARY KEY,
                    advice TEXT,
                    winner TEXT,
                    home_probability REAL,
                    draw_probability REAL,
                    away_probability REAL,
                    total_home TEXT,
                    total_away TEXT,
                    raw_json TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS fixture_match_previews (
                    fixture_id INTEGER PRIMARY KEY,
                    source_hash TEXT NOT NULL,
                    date_time TEXT,
                    date_label TEXT,
                    time_label TEXT,
                    season_label TEXT,
                    league_name TEXT,
                    round_label TEXT,
                    round_api TEXT,
                    venue TEXT,
                    city TEXT,
                    home_logo TEXT,
                    away_logo TEXT,
                    match_label TEXT,
                    home_name TEXT,
                    away_name TEXT,
                    status TEXT,
                    pronostic TEXT,
                    confidence TEXT,
                    score_probable TEXT,
                    summary TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
        )


def register_league_seasons(league_ids, seasons, source: str = "import"):
    _ensure_league_seasons_table()
    now = utc_now().isoformat()
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


_UPDATE_EVENT_LABELS = {
    "championnats_en_cours": "Mise à jour des championnats en cours",
    "historique_auto": "Synchronisation de l’historique",
    "import_manuel": "Import manuel",
    "synchronisation_conseils_api": "Synchronisation des conseils API",
    "synchronisation_xg": "Synchronisation des xG",
    "synchronisation_globale": "Synchronisation globale",
}


def update_log_message(
    event_type: str,
    status: str,
    *,
    reason=None,
    error=None,
    details=None,
) -> str:
    """Produit une explication lisible, même pour les anciens journaux incomplets."""
    for value in (error, reason):
        rendered = "" if value is None else str(value).strip()
        if rendered.casefold() not in {"", "none", "null", "nan", "nat", "<na>"}:
            return rendered

    parsed_details = details
    if isinstance(parsed_details, str):
        try:
            parsed_details = json.loads(parsed_details)
        except (TypeError, ValueError):
            parsed_details = None
    if isinstance(parsed_details, dict):
        for key in ("reason", "message"):
            value = parsed_details.get(key)
            rendered = "" if value is None else str(value).strip()
            if rendered.casefold() not in {"", "none", "null", "nan", "nat", "<na>"}:
                return rendered

    label = _UPDATE_EVENT_LABELS.get(
        str(event_type), str(event_type).replace("_", " ").capitalize()
    )
    normalized_status = str(status).casefold()
    if normalized_status in {"effectuée", "terminee", "terminée", "done", "complete"}:
        return f"{label} terminée avec succès."
    if normalized_status in {"ignorée", "ignoree", "ignored"}:
        return f"{label} ignorée : les données étaient déjà à jour, aucun appel API nécessaire."
    if normalized_status in {"partielle", "partial"}:
        return f"{label} partiellement terminée ; consultez les détails de la tâche."
    if normalized_status in {"en_attente_quota", "waiting_quota"}:
        return f"{label} suspendue jusqu’au renouvellement du quota API."
    if normalized_status in {"erreur", "error"}:
        return f"{label} interrompue par une erreur non détaillée."
    return f"{label} — statut : {status}."


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
    reason = reason or update_log_message(
        event_type,
        status,
        error=error,
        details=details,
    )
    finished_at = finished_at or utc_now().isoformat()
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
        previous = conn.execute(
            text(
                "SELECT id, finished_at FROM update_log "
                "WHERE event_type = :event_type AND status = :status "
                "AND COALESCE(reason, '') = COALESCE(:reason, '') "
                "ORDER BY id DESC LIMIT 1"
            ),
            {"event_type": event_type, "status": status, "reason": reason},
        ).mappings().first()
        if previous:
            try:
                age = datetime.datetime.fromisoformat(
                    finished_at
                ) - datetime.datetime.fromisoformat(previous["finished_at"])
                if 0 <= age.total_seconds() <= 60:
                    return int(previous["id"])
            except (TypeError, ValueError):
                pass
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
        if conn.dialect.name == "postgresql":
            return int(conn.execute(text("SELECT currval(pg_get_serial_sequence('update_log','id'))")).scalar_one())
        return int(result.lastrowid)


def record_update_result(event_type: str, started_at: str, result: dict, error: str | None = None) -> int:
    status = (
        "erreur"
        if error
        else "interrompue_priorité"
        if result.get("preempted")
        else "partielle"
        if result.get("partial")
        else "effectuée"
        if result.get("ran")
        else "ignorée"
    )
    config = result.get("config") or {}
    leagues = result.get("refreshed") or config.get("league_ids")
    return record_update_log(
        event_type=event_type,
        status=status,
        started_at=started_at,
        finished_at=result.get("refreshed_at") or utc_now().isoformat(),
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
    now = utc_now().isoformat()
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
        if conn.dialect.name == "postgresql":
            return int(conn.execute(
                text("INSERT INTO connection_log (connected_at, refreshed_current) VALUES (:connected_at, 0) RETURNING id"),
                {"connected_at": connected_at},
            ).scalar_one())
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
        # An explicit value is authoritative (useful for historical imports);
        # never silently upgrade it to a hard-coded year.
        end_season = int(configured_end_season)
    else:
        current_year = utc_now().year
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
            stored_end_season = int(stored_end_season) if stored_end_season else current_year
            end_season = max(current_year, stored_end_season)
        except Exception:
            end_season = current_year
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
    age = utc_now() - last
    return age.total_seconds() < interval_minutes * 60


def audit_configured_season_access(
    config: dict | None = None,
    progress_callback=None,
    priority_preemption_check=None,
) -> dict:
    config = config or get_auto_refresh_config()
    seasons = list(range(config["start_season"], config["end_season"] + 1))
    sample_league = config["league_ids"][0]
    signature = json.dumps(
        {"sample_league": sample_league, "seasons": seasons},
        sort_keys=True,
        separators=(",", ":"),
    )
    if _get_sync_value("last_api_access_signature") == signature:
        cached = _get_sync_value("last_api_access_audit")
        try:
            result = json.loads(cached) if cached else None
        except (TypeError, ValueError):
            result = None
        if isinstance(result, dict):
            result["cached"] = True
            return result
    accessible = []
    unavailable = []
    total_seasons = max(1, len(seasons))
    if progress_callback:
        progress_callback(0, total_seasons, "Vérification de l’accès aux saisons")
    for index, season in enumerate(seasons, start=1):
        if priority_preemption_check and priority_preemption_check():
            return {
                "sample_league": sample_league,
                "accessible": accessible,
                "unavailable": unavailable,
                "preempted": True,
                "checkpoint": f"season-access:{season}",
            }
        try:
            resp = client.get_fixtures(sample_league, season)
            errors = resp.get("errors") or {}
            if errors:
                unavailable.append({"season": season, "reason": str(errors)})
            else:
                accessible.append(season)
        except Exception as exc:
            if _is_quota_error(exc):
                raise
            unavailable.append({"season": season, "reason": str(exc)})
        if progress_callback:
            progress_callback(
                index,
                total_seasons,
                f"Accès à la saison {season} vérifié",
            )
    summary = {"sample_league": sample_league, "accessible": accessible, "unavailable": unavailable}
    _set_sync_value("last_api_access_audit", json.dumps(summary, ensure_ascii=False))
    _set_sync_value("last_api_access_signature", signature)
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
    # ``Session.get`` ne retrouve pas toujours une instance encore dans
    # ``session.new`` avant le flush. Une réponse /fixtures répète la même
    # équipe sur plusieurs matchs : sans ce contrôle, le commit tente alors
    # plusieurs INSERT pour la même clé primaire.
    try:
        pending_entities = iter(session.new)
    except TypeError:
        pending_entities = iter(())
    team = next(
        (
            candidate
            for candidate in pending_entities
            if isinstance(candidate, models.Team) and candidate.id == tid
        ),
        None,
    )
    if team is None:
        team = session.get(models.Team, tid)
    if team:
        team.name = team_info.get("name") or team_info.get("team", {}).get("name") or team.name
        team.logo = team_info.get("logo") or team_info.get("team", {}).get("logo") or team.logo
        team.country = team_info.get("country") or team_info.get("team", {}).get("country") or team.country
        team.league_id = team.league_id or league_id
        session.add(team)
        return team
    name = team_info.get("name") or team_info.get("team", {}).get("name")
    logo = team_info.get("logo") or team_info.get("team", {}).get("logo")
    country = team_info.get("country") or team_info.get("team", {}).get("country")
    team = models.Team(id=tid, league_id=league_id, name=name, logo=logo, country=country)
    session.add(team)
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


def _save_fixture_api_detail(session, item: dict, league_id: int, season: int) -> None:
    """Réutilise la réponse /fixtures déjà payée pour le cache de détails."""
    fixture = item.get("fixture") or {}
    fixture_id = fixture.get("id") or item.get("id")
    if not fixture_id:
        return
    league = item.get("league") or {}
    venue = fixture.get("venue") or {}
    status = fixture.get("status") or {}
    teams = item.get("teams") or {}
    home = teams.get("home") or {}
    away = teams.get("away") or {}
    session.execute(
        text(
            """
            INSERT INTO fixture_api_details (
                fixture_id, league_id, season, round, venue, city, status_short,
                home_logo, away_logo, league_logo, raw_json, updated_at
            ) VALUES (
                :fixture_id, :league_id, :season, :round, :venue, :city,
                :status_short, :home_logo, :away_logo, :league_logo, :raw_json,
                :updated_at
            )
            ON CONFLICT(fixture_id) DO UPDATE SET
                league_id = excluded.league_id,
                season = excluded.season,
                round = COALESCE(excluded.round, fixture_api_details.round),
                venue = COALESCE(excluded.venue, fixture_api_details.venue),
                city = COALESCE(excluded.city, fixture_api_details.city),
                status_short = COALESCE(excluded.status_short, fixture_api_details.status_short),
                home_logo = COALESCE(excluded.home_logo, fixture_api_details.home_logo),
                away_logo = COALESCE(excluded.away_logo, fixture_api_details.away_logo),
                league_logo = COALESCE(excluded.league_logo, fixture_api_details.league_logo),
                raw_json = excluded.raw_json,
                updated_at = excluded.updated_at
            """
        ),
        {
            "fixture_id": int(fixture_id),
            "league_id": int(league.get("id") or league_id),
            "season": int(league.get("season") or season),
            "round": league.get("round"),
            "venue": venue.get("name"),
            "city": venue.get("city"),
            "status_short": status.get("short"),
            "home_logo": home.get("logo"),
            "away_logo": away.get("logo"),
            "league_logo": league.get("logo"),
            "raw_json": json.dumps(item, ensure_ascii=False, default=str),
            "updated_at": utc_now().isoformat(),
        },
    )


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
                session.commit()
            except Exception as e:
                session.rollback()
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
                    _save_fixture_api_detail(session, item, league_id, season)
                session.commit()
            except Exception as e:
                session.rollback()
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
                session.rollback()
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


def _refresh_league_season(
    session,
    league_id: int,
    season: int,
    pause: float,
    max_retries: int,
    progress_callback=None,
    priority_preemption_check=None,
):
    if priority_preemption_check and priority_preemption_check():
        return {"preempted": True, "checkpoint": f"league-metadata:{league_id}:{season}"}
    league = session.get(models.League, league_id)
    if not league or not league.name or not league.logo:
        _sync_league_metadata(session, league_id, season)

    if priority_preemption_check and priority_preemption_check():
        return {"preempted": True, "checkpoint": f"teams:{league_id}:{season}"}
    tries = 0
    existing_teams = session.query(models.Team).filter_by(
        league_id=league_id
    ).count()
    teams_complete = existing_teams > 0
    failed_phases = []
    while existing_teams <= 0 and tries < max_retries:
        if priority_preemption_check and priority_preemption_check():
            return {"preempted": True, "checkpoint": f"teams:{league_id}:{season}"}
        try:
            resp = client.get_teams(league_id, season)
            if (api_error := _api_error(resp)):
                raise RuntimeError(api_error)
            team_items = resp.get("response", [])
            for item in team_items:
                team_info = item.get("team") if "team" in item else item
                _get_or_create_team(session, team_info, league_id=league_id)
            session.commit()
            existing_teams = 1
            teams_complete = bool(team_items)
            break
        except HTTPError as exc:
            tries += 1
            session.rollback()
            logger.warning(f"HTTP error refreshing teams {league_id}/{season}: {exc} — retry {tries}")
            time.sleep(pause * tries)
        except Exception as exc:
            session.rollback()
            if _is_quota_error(exc):
                raise
            tries += 1
            logger.warning(f"Error refreshing teams {league_id}/{season}: {exc} — retry {tries}")
            time.sleep(pause * tries)

    if not teams_complete:
        failed_phases.append("teams")
    if progress_callback:
        progress_callback(1, 3, f"Équipes {league_id}/{season} vérifiées")

    time.sleep(pause)

    if priority_preemption_check and priority_preemption_check():
        return {"preempted": True, "checkpoint": f"fixtures:{league_id}:{season}"}
    tries = 0
    fixtures_complete = False
    while tries < max_retries:
        if priority_preemption_check and priority_preemption_check():
            return {"preempted": True, "checkpoint": f"fixtures:{league_id}:{season}"}
        try:
            resp = client.get_fixtures(league_id, season)
            if (api_error := _api_error(resp)):
                raise RuntimeError(api_error)
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
                    _save_fixture_api_detail(session, item, league_id, season)
                session.commit()
                if page >= total_pages:
                    break
                if priority_preemption_check and priority_preemption_check():
                    return {"preempted": True, "checkpoint": f"fixtures-page:{league_id}:{season}:{page + 1}"}
                page += 1
                resp = client._get("/fixtures", {"league": league_id, "season": season, "page": page})
                if (api_error := _api_error(resp)):
                    raise RuntimeError(api_error)
                items = resp.get("response", [])
            fixtures_complete = True
            break
        except HTTPError as exc:
            tries += 1
            session.rollback()
            logger.warning(f"HTTP error refreshing fixtures {league_id}/{season}: {exc} — retry {tries}")
            time.sleep(pause * tries)
        except Exception as exc:
            session.rollback()
            if _is_quota_error(exc):
                raise
            tries += 1
            logger.warning(f"Error refreshing fixtures {league_id}/{season}: {exc} — retry {tries}")
            time.sleep(pause * tries)

    if not fixtures_complete:
        failed_phases.append("fixtures")
    if progress_callback:
        progress_callback(2, 3, f"Matchs {league_id}/{season} vérifiés")

    time.sleep(pause)

    if priority_preemption_check and priority_preemption_check():
        return {"preempted": True, "checkpoint": f"standings:{league_id}:{season}"}
    tries = 0
    standings_complete = False
    while tries < max_retries:
        if priority_preemption_check and priority_preemption_check():
            return {"preempted": True, "checkpoint": f"standings:{league_id}:{season}"}
        try:
            resp = client.get_standings(league_id, season)
            if (api_error := _api_error(resp)):
                raise RuntimeError(api_error)
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
            standings_complete = True
            break
        except HTTPError as exc:
            tries += 1
            session.rollback()
            logger.warning(f"HTTP error refreshing standings {league_id}/{season}: {exc} — retry {tries}")
            time.sleep(pause * tries)
        except Exception as exc:
            session.rollback()
            if _is_quota_error(exc):
                raise
            tries += 1
            logger.warning(f"Error refreshing standings {league_id}/{season}: {exc} — retry {tries}")
            time.sleep(pause * tries)

    if not standings_complete:
        failed_phases.append("standings")
    if progress_callback:
        progress_callback(3, 3, f"Classement {league_id}/{season} vérifié")

    return {
        "partial": bool(failed_phases),
        "failed_phases": failed_phases,
        "completed_phases": [
            phase for phase, completed in (
                ("teams", teams_complete),
                ("fixtures", fixtures_complete),
                ("standings", standings_complete),
            ) if completed
        ],
    }


def refresh_current_competitions_on_connection(
    progress_callback=None,
    priority_preemption_check=None,
) -> dict:
    """Refresh active seasons under the same persistent core-scope leases as full sync."""
    config = get_auto_refresh_config()
    if not config["enabled"] or not config["current_enabled"]:
        return {"ran": False, "reason": "Mise à jour des championnats en cours désactivée.", "config": config}
    raw_last_refresh = _get_sync_value("last_current_refresh_utc")
    if raw_last_refresh:
        try:
            last_refresh = datetime.datetime.fromisoformat(raw_last_refresh)
            if (utc_now() - last_refresh).total_seconds() < config["interval_minutes"] * 60:
                return {
                    "ran": False,
                    "reason": "Championnats en cours déjà actualisés récemment.",
                    "config": config,
                    "cached": True,
                }
        except (TypeError, ValueError):
            pass

    from services import remote_sync_lease, sync_registry

    register_league_seasons(config["league_ids"], [config["end_season"]], source="current_refresh")
    session = SessionLocal()
    refreshed = []
    cached_scopes = []
    skipped_scopes = []
    league_ids = config["league_ids"]
    total_steps = max(1, len(league_ids) * 3)
    lease_seconds = 1800
    renew_interval = 60

    if progress_callback:
        progress_callback(0, total_steps, "Préparation des championnats en cours")
    try:
        for league_index, league_id in enumerate(league_ids):
            season = _active_season_for_league(session, league_id, config["end_season"])
            key = f"core:{int(league_id)}:{int(season)}"
            scope_state_key = f"current-refresh:{int(league_id)}:{int(season)}"
            if priority_preemption_check and priority_preemption_check():
                return {
                    "ran": True,
                    "preempted": True,
                    "checkpoint": f"current-refresh:{league_id}:{season}",
                    "config": config,
                    "refreshed": refreshed,
                    "cached_scopes": cached_scopes,
                    "skipped_scopes": skipped_scopes,
                }

            prior_state = sync_registry.get(scope_state_key)
            if prior_state and prior_state.get("status") == "complete":
                try:
                    updated_at = datetime.datetime.fromisoformat(prior_state["updated_at"])
                    if updated_at.tzinfo is not None:
                        updated_at = updated_at.astimezone(datetime.UTC).replace(tzinfo=None)
                    if (
                        (utc_now() - updated_at).total_seconds()
                        < config["interval_minutes"] * 60
                    ):
                        cached_scopes.append({"league_id": int(league_id), "season": int(season)})
                        if progress_callback:
                            progress_callback(
                                min(total_steps, (league_index + 1) * 3),
                                total_steps,
                                f"Ligue {league_id} - {season}: actualisation récente réutilisée",
                            )
                        continue
                except (KeyError, TypeError, ValueError):
                    pass

            owner = f"current-refresh:{os.getpid()}:{time.time_ns()}"
            try:
                local_claimed = sync_registry.claim_download(
                    key, owner, lease_seconds=lease_seconds
                )
            except Exception as exc:
                logger.warning("Unable to claim local current-refresh lease %s: %s", key, exc)
                skipped_scopes.append(key)
                continue
            if not local_claimed:
                skipped_scopes.append(key)
                if progress_callback:
                    progress_callback(
                        min(total_steps, (league_index + 1) * 3),
                        total_steps,
                        f"Ligue {league_id} - {season}: lease locale persistante détenue par un autre worker",
                    )
                continue

            remote_claimed = False
            try:
                remote_claimed = remote_sync_lease.claim(
                    key, owner, lease_seconds=lease_seconds
                )
            except Exception as exc:
                logger.warning("Remote current-refresh lease unavailable for %s: %s", key, exc)
            if not remote_claimed:
                try:
                    sync_registry.release_download_claim(key, owner)
                except Exception:
                    logger.warning("Unable to release local current-refresh lease %s", key, exc_info=True)
                skipped_scopes.append(key)
                if progress_callback:
                    progress_callback(
                        min(total_steps, (league_index + 1) * 3),
                        total_steps,
                        f"Ligue {league_id} - {season}: lease distante indisponible ou détenue par un autre worker",
                    )
                continue

            lease_lost = False
            last_local_renewed = time.monotonic()
            last_remote_renewed = last_local_renewed

            def _renew_scope(*, force=False):
                nonlocal lease_lost, last_local_renewed, last_remote_renewed
                now = time.monotonic()
                if not force and now - min(last_local_renewed, last_remote_renewed) < renew_interval:
                    return not lease_lost
                try:
                    local_ok = sync_registry.renew_download_claim(
                        key, owner, lease_seconds=lease_seconds
                    )
                except Exception:
                    local_ok = False
                if not local_ok:
                    lease_lost = True
                    return False
                last_local_renewed = now
                try:
                    remote_ok = remote_sync_lease.renew(
                        key, owner, lease_seconds=lease_seconds
                    )
                except Exception:
                    remote_ok = False
                if not remote_ok:
                    lease_lost = True
                    return False
                last_remote_renewed = now
                return True

            def _renew_and_check_priority():
                if not _renew_scope():
                    return True
                return bool(
                    priority_preemption_check
                    and priority_preemption_check()
                )

            def _scoped_progress(current, _scope_total, label):
                if not _renew_scope():
                    logger.warning("Current-refresh scope lease lost at checkpoint %s", key)
                if progress_callback:
                    progress_callback(
                        min(total_steps, league_index * 3 + int(current or 0)),
                        total_steps,
                        label,
                    )

            try:
                sync_registry.mark(
                    scope_state_key,
                    "current_season",
                    "running",
                    message="Leases persistantes locale et distante acquises",
                )
                logger.info("Refreshing active competition %s season %s", league_id, season)
                refresh_result = _refresh_league_season(
                    session,
                    league_id,
                    season,
                    pause=config["pause"],
                    max_retries=config["max_retries"],
                    progress_callback=_scoped_progress,
                    priority_preemption_check=_renew_and_check_priority,
                )
                if lease_lost:
                    skipped_scopes.append(key)
                    sync_registry.mark(
                        scope_state_key,
                        "current_season",
                        "deferred",
                        message="Lease persistante perdue ; reprise requise.",
                    )
                    return {
                        "ran": True,
                        "partial": True,
                        "lease_lost": True,
                        "checkpoint": (refresh_result or {}).get("checkpoint") or f"current-refresh:{league_id}:{season}",
                        "config": config,
                        "refreshed": refreshed,
                        "cached_scopes": cached_scopes,
                        "skipped_scopes": skipped_scopes,
                        "reason": "Lease persistante perdue ; arrêt sûr et reprise requise.",
                    }
                if refresh_result and refresh_result.get("partial"):
                    failed_phases = list(refresh_result.get("failed_phases") or [])
                    skipped_scopes.append(key)
                    reason = (
                        "Actualisation incomplète ; phases à reprendre : "
                        + ", ".join(failed_phases or ["inconnues"])
                    )
                    sync_registry.mark(
                        scope_state_key,
                        "current_season",
                        "deferred",
                        message=reason,
                    )
                    return {
                        "ran": True,
                        "partial": True,
                        "checkpoint": f"current-refresh:{league_id}:{season}",
                        "failed_phases": failed_phases,
                        "config": config,
                        "refreshed": refreshed,
                        "cached_scopes": cached_scopes,
                        "skipped_scopes": skipped_scopes,
                        "reason": reason,
                    }
                if refresh_result and refresh_result.get("preempted"):
                    sync_registry.mark(
                        scope_state_key,
                        "current_season",
                        "deferred",
                        message=f"Préemption au checkpoint {refresh_result.get('checkpoint')}.",
                    )
                    return {
                        "ran": True,
                        "preempted": True,
                        "checkpoint": refresh_result.get("checkpoint"),
                        "config": config,
                        "refreshed": refreshed,
                        "cached_scopes": cached_scopes,
                        "skipped_scopes": skipped_scopes,
                    }
                sync_registry.mark(
                    scope_state_key,
                    "current_season",
                    "complete",
                    message="Actualisation de la saison courante terminée.",
                )
                refreshed.append({"league_id": league_id, "season": season})
            except Exception as exc:
                try:
                    sync_registry.mark(
                        scope_state_key,
                        "current_season",
                        "error",
                        message=str(exc),
                    )
                except Exception:
                    logger.warning("Unable to record current-refresh error for %s", key, exc_info=True)
                raise
            finally:
                if remote_claimed:
                    try:
                        remote_sync_lease.release(key, owner)
                    except Exception:
                        logger.warning("Unable to release remote current-refresh lease %s", key, exc_info=True)
                try:
                    sync_registry.release_download_claim(key, owner)
                except Exception:
                    logger.warning("Unable to release local current-refresh lease %s", key, exc_info=True)
            time.sleep(config["pause"])
    finally:
        session.close()

    if skipped_scopes:
        return {
            "ran": True,
            "partial": True,
            "reason": "Certaines ligues/saisons ont été différées faute de lease ; nouvelle tentative nécessaire.",
            "config": config,
            "refreshed": refreshed,
            "cached_scopes": cached_scopes,
            "skipped_scopes": skipped_scopes,
        }

    now = utc_now().isoformat()
    _set_sync_value("last_current_refresh_utc", now)
    return {
        "ran": True,
        "reason": "Championnats en cours mis à jour.",
        "config": config,
        "refreshed": refreshed,
        "cached_scopes": cached_scopes,
        "refreshed_at": now,
    }

def import_multiple_leagues(league_ids: List[int], seasons: List[int] | None = None):
    seasons = seasons or _configured_season_range()
    for lid in league_ids:
        import_league_seasons_by_id(lid, seasons=seasons)


def _manual_import_resume_plan(league_ids, seasons, checkpoint):
    """Parse a manual-import checkpoint into a validated ordered resume position."""
    if not checkpoint:
        return None
    parts = str(checkpoint).split(":")
    if len(parts) < 3 or parts[0] not in {
        "core", "teams", "fixtures", "fixtures-page", "standings"
    }:
        return None
    phase = parts[0]
    if (phase == "fixtures-page" and len(parts) != 4) or (
        phase != "fixtures-page" and len(parts) != 3
    ):
        return None
    try:
        league_id = int(parts[1])
        season = int(parts[2])
        page = int(parts[3]) if phase == "fixtures-page" else 1
        if league_id <= 0 or season <= 0 or page <= 0:
            return None
        league_index = list(league_ids).index(league_id)
        season_index = list(seasons).index(season)
    except (TypeError, ValueError, IndexError):
        return None
    return {
        "league_id": league_id,
        "season": season,
        "league_index": league_index,
        "season_index": season_index,
        "phase": phase,
        "page": page,
    }


def _import_leagues_cautious_unleased(
    league_ids: List[int],
    seasons: List[int] | None = None,
    pause: float = 1.5,
    max_retries: int = 5,
    force_refresh_seasons: List[int] | None = None,
    progress_callback=None,
    priority_preemption_check=None,
    resume_checkpoint=None,
):
    """Import several leagues with polite pauses, retries and logging.

    - `pause`: base seconds between API calls
    - `max_retries`: number of retries on 429 or transient errors
    """
    seasons = seasons or _configured_season_range()
    register_league_seasons(league_ids, seasons, source="import")
    session = SessionLocal()
    force_refresh_seasons = set(force_refresh_seasons or [])
    resume_plan = _manual_import_resume_plan(league_ids, seasons, resume_checkpoint)
    # The interrupted scope is refreshed locally below; do not force that
    # season across every other league in the import.
    total_steps = max(1, len(league_ids) * len(seasons) * 3)
    completed_steps = 0

    def _progress(label: str, increment: int = 0):
        nonlocal completed_steps
        completed_steps = min(total_steps, completed_steps + increment)
        if progress_callback:
            progress_callback(completed_steps, total_steps, label)

    try:
        for league_index, lid in enumerate(league_ids):
            if resume_plan and league_index < resume_plan["league_index"]:
                continue
            logging.info(f"Starting import for league {lid}")
            for season_index, season in enumerate(seasons):
                if resume_plan and (
                    league_index < resume_plan["league_index"]
                    or (
                        league_index == resume_plan["league_index"]
                        and season_index < resume_plan["season_index"]
                    )
                ):
                    continue
                resume_scope = bool(
                    resume_plan
                    and league_index == resume_plan["league_index"]
                    and season_index == resume_plan["season_index"]
                )
                resume_phase = resume_plan["phase"] if resume_scope else None
                resume_page = resume_plan["page"] if resume_scope and resume_phase == "fixtures-page" else 1
                force_refresh_scope = season in force_refresh_seasons or resume_scope
                logging.info(f"Importing {lid} season {season}")
                if priority_preemption_check and priority_preemption_check():
                    return {"preempted": True, "checkpoint": f"core:{lid}:{season}", "league_id": lid, "season": season}
                _progress(f"Ligue {lid} - {season}: préparation")
                # Check existing
                existing_count = session.query(models.Match).filter_by(league_id=lid, season=season).count()
                team_count = session.query(models.Team).filter_by(league_id=lid).count()
                standing_count = session.query(models.Standing).filter_by(league_id=lid, season=season).count()
                complete_core = existing_count > 0 and team_count > 0 and standing_count > 0
                if complete_core and not force_refresh_scope:
                    logging.info(f"Season {season} déjà complète ({existing_count} matchs, {team_count} équipes, {standing_count} classements) — ignorée")
                    _progress(f"Ligue {lid} - {season}: déjà en base ({existing_count} matchs)", increment=3)
                    continue

                league = session.get(models.League, lid)
                if not league or not league.name or not league.logo:
                    _sync_league_metadata(session, lid, season)

                # Teams
                if priority_preemption_check and priority_preemption_check():
                    return {"preempted": True, "checkpoint": f"teams:{lid}:{season}", "league_id": lid, "season": season}
                _progress(f"Ligue {lid} - {season}: téléchargement des équipes")
                tries = 0
                skip_teams = resume_scope and resume_phase in {
                    "fixtures", "fixtures-page", "standings"
                }
                teams_ok = team_count > 0 or skip_teams
                last_error = None
                while not teams_ok and tries < max_retries:
                    try:
                        resp = client.get_teams(lid, season)
                        if (api_error := _api_error(resp)):
                            raise RuntimeError(api_error)
                        team_items = resp.get('response', [])
                        for t in team_items:
                            team_info = t.get('team') if 'team' in t else t
                            _get_or_create_team(session, team_info, league_id=lid)
                        session.commit()
                        teams_ok = bool(team_items)
                        break
                    except HTTPError as e:
                        last_error = e
                        tries += 1
                        session.rollback()
                        logging.warning(f"HTTP error fetching teams {lid}/{season}: {e} — retry {tries}")
                        time.sleep(pause * tries)
                    except Exception as e:
                        last_error = e
                        session.rollback()
                        if _is_quota_error(e):
                            raise
                        tries += 1
                        logging.warning(f"Error fetching teams {lid}/{season}: {e} — retry {tries}")
                        time.sleep(pause * tries)
                if not teams_ok:
                    detail = f" Dernière erreur : {last_error}" if last_error else ""
                    raise RuntimeError(
                        f"Échec du téléchargement des équipes ({lid}/{season}) "
                        f"après {max_retries} tentatives.{detail}"
                    )
                _progress(f"Ligue {lid} - {season}: équipes traitées", increment=1)

                time.sleep(pause)

                # Fixtures
                if priority_preemption_check and priority_preemption_check():
                    return {"preempted": True, "checkpoint": f"fixtures:{lid}:{season}", "league_id": lid, "season": season}
                _progress(f"Ligue {lid} - {season}: téléchargement des matchs")
                tries = 0
                skip_fixtures = resume_scope and resume_phase == "standings"
                fixtures_ok = skip_fixtures or (
                    existing_count > 0 and not force_refresh_scope
                )
                last_error = None
                while not fixtures_ok and tries < max_retries:
                    try:
                        resuming_fixture_page = bool(
                            resume_scope
                            and resume_phase == "fixtures-page"
                            and resume_page > 1
                        )
                        if resuming_fixture_page:
                            resp = client._get(
                                "/fixtures",
                                {"league": lid, "season": season, "page": resume_page},
                            )
                        else:
                            resp = client.get_fixtures(lid, season)
                        if (api_error := _api_error(resp)):
                            raise RuntimeError(api_error)
                        items = resp.get('response', [])
                        # Support paging if present. A persisted page can become stale
                        # when the API changes its page count; restart safely rather
                        # than treating an out-of-range page as a complete import.
                        paging = resp.get('paging') or {}
                        page = resume_page if resuming_fixture_page else 1
                        raw_total_pages = paging.get('total')
                        try:
                            total_pages = max(1, int(raw_total_pages or 1))
                        except (TypeError, ValueError):
                            total_pages = 1
                        if resuming_fixture_page:
                            raw_current_page = paging.get('current')
                            try:
                                current_page = (
                                    int(raw_current_page)
                                    if raw_current_page is not None
                                    else None
                                )
                            except (TypeError, ValueError):
                                current_page = None
                            checkpoint_valid = (
                                raw_total_pages is not None
                                and page <= total_pages
                                and (
                                    raw_current_page is None
                                    or current_page == page
                                )
                            )
                            if not checkpoint_valid:
                                logging.warning(
                                    "Invalid/stale fixtures checkpoint %s/%s page %s "
                                    "(API current=%r total=%r); restarting from page 1",
                                    lid,
                                    season,
                                    page,
                                    raw_current_page,
                                    raw_total_pages,
                                )
                                resp = client.get_fixtures(lid, season)
                                if (api_error := _api_error(resp)):
                                    raise RuntimeError(api_error)
                                items = resp.get('response', [])
                                paging = resp.get('paging') or {}
                                page = 1
                                try:
                                    total_pages = max(1, int(paging.get('total') or 1))
                                except (TypeError, ValueError):
                                    total_pages = 1
                        while True:
                            for item in items:
                                teams = item.get('teams') or {}
                                if isinstance(teams, dict):
                                    if teams.get('home'):
                                        _get_or_create_team(session, teams.get('home'), league_id=lid)
                                    if teams.get('away'):
                                        _get_or_create_team(session, teams.get('away'), league_id=lid)
                                _save_match(session, item, lid, season)
                                _save_fixture_api_detail(session, item, lid, season)
                            session.commit()
                            if page >= total_pages:
                                break
                            if priority_preemption_check and priority_preemption_check():
                                return {"preempted": True, "checkpoint": f"fixtures-page:{lid}:{season}:{page + 1}", "league_id": lid, "season": season}
                            page += 1
                            resp = client._get('/fixtures', {'league': lid, 'season': season, 'page': page})
                            if (api_error := _api_error(resp)):
                                raise RuntimeError(api_error)
                            items = resp.get('response', [])
                        # An empty response is valid only for a genuinely empty
                        # competition; keep it retryable so missing data is not
                        # silently marked complete.
                        fixtures_ok = bool(items) or bool(session.query(models.Match).filter_by(league_id=lid, season=season).count())
                        break
                    except HTTPError as e:
                        last_error = e
                        tries += 1
                        session.rollback()
                        logging.warning(f"HTTP error fetching fixtures {lid}/{season}: {e} — retry {tries}")
                        time.sleep(pause * tries)
                    except Exception as e:
                        last_error = e
                        session.rollback()
                        if _is_quota_error(e):
                            raise
                        tries += 1
                        logging.warning(f"Error fetching fixtures {lid}/{season}: {e} — retry {tries}")
                        time.sleep(pause * tries)
                if not fixtures_ok:
                    detail = f" Dernière erreur : {last_error}" if last_error else ""
                    raise RuntimeError(
                        f"Échec du téléchargement des matchs ({lid}/{season}) "
                        f"après {max_retries} tentatives.{detail}"
                    )
                _progress(f"Ligue {lid} - {season}: matchs traités", increment=1)

                time.sleep(pause)

                # Standings
                if priority_preemption_check and priority_preemption_check():
                    return {"preempted": True, "checkpoint": f"standings:{lid}:{season}", "league_id": lid, "season": season}
                _progress(f"Ligue {lid} - {season}: téléchargement du classement")
                tries = 0
                standings_ok = (
                    standing_count > 0 and not force_refresh_scope
                )
                last_error = None
                while not standings_ok and tries < max_retries:
                    try:
                        resp = client.get_standings(lid, season)
                        if (api_error := _api_error(resp)):
                            raise RuntimeError(api_error)
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
                        standings_ok = bool(session.query(models.Standing).filter_by(league_id=lid, season=season).count())
                        break
                    except HTTPError as e:
                        last_error = e
                        tries += 1
                        session.rollback()
                        logging.warning(f"HTTP error fetching standings {lid}/{season}: {e} — retry {tries}")
                        time.sleep(pause * tries)
                    except Exception as e:
                        last_error = e
                        session.rollback()
                        if _is_quota_error(e):
                            raise
                        tries += 1
                        logging.warning(f"Error fetching standings {lid}/{season}: {e} — retry {tries}")
                        time.sleep(pause * tries)
                if not standings_ok:
                    detail = f" Dernière erreur : {last_error}" if last_error else ""
                    raise RuntimeError(
                        f"Échec du téléchargement du classement ({lid}/{season}) "
                        f"après {max_retries} tentatives.{detail}"
                    )
                _progress(f"Ligue {lid} - {season}: classement traité", increment=1)

                # polite pause between seasons
                time.sleep(pause * 2)
        _progress("Import terminé")
    finally:
        session.close()



def import_leagues_cautious(
    league_ids: List[int],
    seasons: List[int] | None = None,
    pause: float = 1.5,
    max_retries: int = 5,
    force_refresh_seasons: List[int] | None = None,
    progress_callback=None,
    priority_preemption_check=None,
    resume_checkpoint=None,
):
    """Import league-season scopes under cross-process persistent leases.

    Each scope uses the same resource key as the full-sync registry. A contending
    worker is skipped without issuing API calls; leases are renewed at phase/page
    checkpoints and always released by their owner.
    """
    from services import remote_sync_lease, sync_registry

    seasons = seasons or _configured_season_range()
    register_league_seasons(league_ids, seasons, source="import")
    resume_plan = _manual_import_resume_plan(league_ids, seasons, resume_checkpoint)
    force_refresh_seasons = set(force_refresh_seasons or [])
    total_steps = max(1, len(league_ids) * len(seasons) * 3)
    skipped_scopes = []
    completed_scopes = []
    lease_seconds = 1800

    class _CoreImportLeaseLost(Exception):
        pass

    for league_index, league_id in enumerate(league_ids):
        if resume_plan and league_index < resume_plan["league_index"]:
            continue
        for season_index, season in enumerate(seasons):
            if resume_plan and (
                league_index < resume_plan["league_index"]
                or (
                    league_index == resume_plan["league_index"]
                    and season_index < resume_plan["season_index"]
                )
            ):
                continue

            scope_index = league_index * len(seasons) + season_index
            key = f"core:{int(league_id)}:{int(season)}"
            resume_scope = bool(
                resume_plan
                and league_index == resume_plan["league_index"]
                and season_index == resume_plan["season_index"]
            )
            owner = f"core-import:{os.getpid()}:{time.time_ns()}"
            if not sync_registry.claim_download(
                key, owner, lease_seconds=lease_seconds
            ):
                skipped_scopes.append(key)
                if progress_callback:
                    progress_callback(
                        min(total_steps, (scope_index + 1) * 3),
                        total_steps,
                        f"Ligue {league_id} - {season}: lease locale persistante déjà détenue par un autre worker",
                    )
                continue

            remote_claimed = False
            try:
                remote_claimed = remote_sync_lease.claim(
                    key, owner, lease_seconds=lease_seconds
                )
            except Exception:
                try:
                    sync_registry.release_download_claim(key, owner)
                except Exception:
                    logger.warning(
                        "Unable to release local core import lease after remote claim failure %s",
                        key,
                        exc_info=True,
                    )
                raise
            if not remote_claimed:
                try:
                    sync_registry.release_download_claim(key, owner)
                except Exception:
                    logger.warning("Unable to release local core import lease %s", key, exc_info=True)
                skipped_scopes.append(key)
                if progress_callback:
                    progress_callback(
                        min(total_steps, (scope_index + 1) * 3),
                        total_steps,
                        f"Ligue {league_id} - {season}: lease distante persistante détenue par un autre worker",
                    )
                continue

            lease_lost = False
            last_remote_renewed = time.monotonic()
            remote_renew_interval = 60

            def _renew_remote(*, force=False):
                nonlocal lease_lost, last_remote_renewed
                now = time.monotonic()
                if not force and now - last_remote_renewed < remote_renew_interval:
                    return True
                try:
                    renewed = remote_sync_lease.renew(
                        key, owner, lease_seconds=lease_seconds
                    )
                except Exception:
                    renewed = False
                if not renewed:
                    lease_lost = True
                    return False
                last_remote_renewed = now
                return True

            def _renew_and_check_priority():
                nonlocal lease_lost
                try:
                    renewed = sync_registry.renew_download_claim(
                        key, owner, lease_seconds=lease_seconds
                    )
                except Exception:
                    renewed = False
                if not renewed:
                    lease_lost = True
                    return True
                if not _renew_remote():
                    return True
                return bool(
                    priority_preemption_check
                    and priority_preemption_check()
                )

            def _scoped_progress(current, _scope_total, label):
                nonlocal lease_lost
                try:
                    renewed = sync_registry.renew_download_claim(
                        key, owner, lease_seconds=lease_seconds
                    )
                except Exception:
                    renewed = False
                if not renewed or not _renew_remote():
                    lease_lost = True
                    raise _CoreImportLeaseLost(key)
                if progress_callback:
                    progress_callback(
                        min(total_steps, scope_index * 3 + int(current or 0)),
                        total_steps,
                        label,
                    )

            try:
                sync_registry.mark(
                    key, "core_season", "running",
                    message="Leases persistantes locale et distante acquises",
                )
                result = _import_leagues_cautious_unleased(
                    [league_id],
                    seasons=[season],
                    pause=pause,
                    max_retries=max_retries,
                    force_refresh_seasons=(
                        [season] if season in force_refresh_seasons else None
                    ),
                    progress_callback=_scoped_progress,
                    priority_preemption_check=_renew_and_check_priority,
                    resume_checkpoint=resume_checkpoint if resume_scope else None,
                )
                if lease_lost:
                    skipped_scopes.append(key)
                    if progress_callback:
                        progress_callback(
                            min(total_steps, (scope_index + 1) * 3),
                            total_steps,
                            f"Ligue {league_id} - {season}: lease perdue, reprise nécessaire",
                        )
                    return {
                        "skipped": True,
                        "skipped_scopes": skipped_scopes,
                        "completed_scopes": completed_scopes,
                        "checkpoint": (result or {}).get("checkpoint") or key,
                        "lease_lost": True,
                    }
                if result and result.get("preempted"):
                    sync_registry.mark(
                        key, "core_season", "partial",
                        message="Checkpoint de préemption",
                        metadata=result,
                    )
                    return result
                sync_registry.mark(key, "core_season", "complete", item_count=1)
                completed_scopes.append(key)
            except _CoreImportLeaseLost:
                skipped_scopes.append(key)
                return {
                    "skipped": True,
                    "skipped_scopes": skipped_scopes,
                    "completed_scopes": completed_scopes,
                    "checkpoint": key,
                    "lease_lost": True,
                }
            except Exception as exc:
                if not lease_lost:
                    sync_registry.mark(
                        key, "core_season", "error", message=str(exc)
                    )
                raise
            finally:
                try:
                    remote_sync_lease.release(key, owner)
                except Exception:
                    logger.warning("Unable to release remote core import lease %s", key, exc_info=True)
                try:
                    sync_registry.release_download_claim(key, owner)
                except Exception:
                    logger.warning("Unable to release local core import lease %s", key, exc_info=True)

    if skipped_scopes:
        return {
            "skipped": True,
            "skipped_scopes": skipped_scopes,
            "completed_scopes": completed_scopes,
        }
    return None



def auto_refresh_if_due(
    force: bool = False,
    progress_callback=None,
    skip_force_refresh_seasons=None,
    priority_preemption_check=None,
) -> dict:
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

    def report_phase(current, total, start, end, label):
        if not progress_callback:
            return
        ratio = min(1.0, max(0.0, current / max(1, total)))
        progress_callback(
            int(round(start + (end - start) * ratio)),
            100,
            label,
        )

    audit = audit_configured_season_access(
        config,
        progress_callback=lambda current, total, label: report_phase(
            current,
            total,
            0,
            15,
            label,
        ),
        priority_preemption_check=priority_preemption_check,
    )
    if audit.get("preempted"):
        return {
            "ran": False,
            "preempted": True,
            "checkpoint": audit.get("checkpoint"),
            "config": config,
            "audit": audit,
        }
    seasons = audit["accessible"]
    if not seasons:
        _set_sync_value("last_auto_refresh_utc", utc_now().isoformat())
        return {
            "ran": False,
            "reason": "Aucune saison configurée n’est accessible avec le plan API actuel.",
            "config": config,
            "audit": audit,
        }

    skipped_force_seasons = {
        int(value) for value in (skip_force_refresh_seasons or [])
    }
    force_refresh_seasons = [
        season
        for season in seasons[-config["recent_seasons"] :]
        if int(season) not in skipped_force_seasons
    ]

    import_result = import_leagues_cautious(
        config["league_ids"],
        seasons=seasons,
        pause=config["pause"],
        max_retries=config["max_retries"],
        force_refresh_seasons=force_refresh_seasons,
        progress_callback=lambda current, total, label: report_phase(
            current,
            total,
            15,
            100,
            label,
        ),
        priority_preemption_check=priority_preemption_check,
    )
    if import_result and import_result.get("preempted"):
        return {
            "ran": False,
            "preempted": True,
            "checkpoint": import_result.get("checkpoint"),
            "config": config,
            "seasons": seasons,
            "force_refresh_seasons": force_refresh_seasons,
            "audit": audit,
            "import": import_result,
        }
    now = utc_now().isoformat()
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
