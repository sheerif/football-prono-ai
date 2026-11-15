"""Configuration de la base SQLite locale de l'application."""

from __future__ import annotations

import gzip
import hashlib
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import declarative_base, sessionmaker

load_dotenv()
_streamlit_cloud = os.path.isdir("/mount/src") or bool(
    os.getenv("STREAMLIT_SHARING_MODE")
)
_configured_database_url = os.getenv("DATABASE_URL")
if _configured_database_url:
    DATABASE_URL = _configured_database_url
elif _streamlit_cloud:
    # Cloud storage is local and ephemeral; keep the runtime DB outside the
    # checked-out source tree and initialize it from the bundled SQLite copy.
    DATABASE_URL = "sqlite:////tmp/football.db"
else:
    DATABASE_URL = "sqlite:///football.db"
_url = make_url(DATABASE_URL)
if (
    _url.get_backend_name() != "sqlite"
    or _url.drivername not in {"sqlite", "sqlite+pysqlite"}
    or _url.host is not None
):
    raise RuntimeError("Seule une base SQLite locale est prise en charge.")


def _seed_cloud_database() -> None:
    """Initialize an empty cloud-local database from the bundled data snapshot."""
    if not _streamlit_cloud or not _url.database or _url.database == ":memory:":
        return
    database_path = Path(_url.database)
    if not database_path.is_absolute():
        database_path = Path.cwd() / database_path
    if database_path.is_file() and database_path.stat().st_size > 0:
        return

    archive_path = Path(__file__).with_name("seed") / "football.db.gz"
    if not archive_path.is_file():
        return
    database_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = database_path.with_name(f"{database_path.name}.seed.tmp")
    digest = hashlib.sha256()
    try:
        with gzip.open(archive_path, "rb") as source, temporary_path.open("wb") as target:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
                target.write(chunk)
        if digest.hexdigest() != "d7930409149acf6c211702cab896144fe2aa715380c711e3db06d87fc8c948b8":
            raise RuntimeError("L’instantané SQLite embarqué est endommagé.")
        os.replace(temporary_path, database_path)
    finally:
        temporary_path.unlink(missing_ok=True)


_seed_cloud_database()

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
