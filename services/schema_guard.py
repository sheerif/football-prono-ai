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
