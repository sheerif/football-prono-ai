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
}


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


def ensure_fixture_api_cache_tables() -> None:
    with engine.begin() as conn:
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
