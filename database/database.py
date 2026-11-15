"""Configuration de la base SQLite locale de l'application."""

from __future__ import annotations

import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import declarative_base, sessionmaker

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///football.db")
_url = make_url(DATABASE_URL)
if (
    _url.get_backend_name() != "sqlite"
    or _url.drivername not in {"sqlite", "sqlite+pysqlite"}
    or _url.host is not None
):
    raise RuntimeError("Seule une base SQLite locale est prise en charge.")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 30},
    pool_pre_ping=True,
)


@event.listens_for(engine, "connect")
def _configure_sqlite_connection(dbapi_connection, _connection_record):
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA journal_mode=WAL")
    finally:
        cursor.close()


def persistence_revision() -> int:
    """Return a small marker used to refresh cached pages after local writes."""
    try:
        with engine.connect() as connection:
            value = connection.exec_driver_sql(
                "SELECT id FROM update_log ORDER BY id DESC LIMIT 1"
            ).scalar_one_or_none()
        return int(value or 0)
    except Exception:
        # The update log may not exist during first-time schema creation.
        return 0


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
