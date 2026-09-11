import functools
import threading

from sqlalchemy import text, inspect

from database import models
from database.database import engine


PERFORMANCE_INDEX_NAMES = {
    "ix_teams_league_id",
    "ix_matches_league_season_date",
    "ix_matches_home_team_date",
    "ix_matches_away_team_date",
    "ix_matches_date_scores",
    "ix_player_statistics_league_season_team",
    "ix_fixture_team_statistics_team",
    "ix_xg_ingestion_audit_fixture",
    "ix_xg_ingestion_audit_run",
}

_guard_lock = threading.RLock()
_completed_guards: set[str] = set()


def _once_per_process(func):
    """Avoid repeating remote schema introspection on every Streamlit rerun."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with _guard_lock:
            if func.__name__ in _completed_guards:
                return None
            result = func(*args, **kwargs)
            _completed_guards.add(func.__name__)
            return result
    return wrapper


@_once_per_process
def ensure_match_score_columns() -> None:
    with engine.begin() as conn:
        inspector = inspect(conn)
        if not inspector.has_table("matches"):
            return

        match_columns = {column["name"] for column in inspector.get_columns("matches")}
        missing_match_columns = {
            "home_goals": "INTEGER",
            "away_goals": "INTEGER",
            "winner": "TEXT",
            "status": "TEXT",
        }
        for column_name, column_type in missing_match_columns.items():
            if column_name not in match_columns:
                conn.execute(text(f"ALTER TABLE matches ADD COLUMN {column_name} {column_type}"))


@_once_per_process
def ensure_performance_indexes() -> None:
    """Create indexes added after the initial tables without rebuilding data."""
    indexes = (
        index
        for table in models.Base.metadata.sorted_tables
        for index in table.indexes
        if index.name in PERFORMANCE_INDEX_NAMES
    )
    for index in indexes:
        index.create(bind=engine, checkfirst=True)


@_once_per_process
def ensure_fixture_api_cache_tables() -> None:
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
