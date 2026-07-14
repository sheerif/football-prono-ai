from sqlalchemy import text

from database.database import engine


def ensure_match_score_columns() -> None:
    with engine.begin() as conn:
        table_exists = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'matches'")
        ).fetchone()
        if not table_exists:
            return

        match_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(matches)")).fetchall()}
        missing_match_columns = {
            "home_goals": "INTEGER",
            "away_goals": "INTEGER",
            "winner": "TEXT",
            "status": "TEXT",
        }
        for column_name, column_type in missing_match_columns.items():
            if column_name not in match_columns:
                conn.execute(text(f"ALTER TABLE matches ADD COLUMN {column_name} {column_type}"))


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
