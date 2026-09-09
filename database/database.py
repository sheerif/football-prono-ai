import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker, declarative_base

load_dotenv()
_turso_url = (os.getenv("TURSO_DATABASE_URL") or "").strip()
_turso_token = (os.getenv("TURSO_AUTH_TOKEN") or "").strip()
if _turso_url:
    DATABASE_URL = (
        _turso_url
        if _turso_url.startswith("sqlite+libsql://")
        else f"sqlite+{_turso_url}"
    )
    if "secure=" not in DATABASE_URL:
        DATABASE_URL += ("&" if "?" in DATABASE_URL else "?") + "secure=true"
else:
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///football.db")

# ``check_same_thread`` is a SQLite-only option; passing it to a PostgreSQL
# driver prevents the application from starting when DATABASE_URL is changed.
_url = make_url(DATABASE_URL)
_is_sqlite = _url.get_backend_name() == "sqlite"
_is_local_sqlite = _url.drivername == "sqlite"
_connect_args = (
    {"auth_token": _turso_token}
    if _turso_url
    else ({"check_same_thread": False, "timeout": 30} if _is_sqlite else {})
)
engine = create_engine(DATABASE_URL, connect_args=_connect_args, pool_pre_ping=True)


if _is_local_sqlite:
    @event.listens_for(engine, "connect")
    def _configure_sqlite_connection(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA journal_mode=WAL")
        finally:
            cursor.close()


def persistence_mode() -> str:
    if _turso_url:
        return "turso"
    if _is_local_sqlite:
        return "sqlite_local"
    return _url.get_backend_name()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
