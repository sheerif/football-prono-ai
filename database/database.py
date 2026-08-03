import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker, declarative_base

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///football.db")

# ``check_same_thread`` is a SQLite-only option; passing it to a PostgreSQL
# driver prevents the application from starting when DATABASE_URL is changed.
_url = make_url(DATABASE_URL)
_is_sqlite = _url.get_backend_name() == "sqlite"
_connect_args = (
    {"check_same_thread": False, "timeout": 30}
    if _is_sqlite
    else {}
)
engine = create_engine(DATABASE_URL, connect_args=_connect_args, pool_pre_ping=True)


if _is_sqlite:
    @event.listens_for(engine, "connect")
    def _configure_sqlite_connection(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA journal_mode=WAL")
        finally:
            cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
