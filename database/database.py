import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import QueuePool

load_dotenv()
_turso_url = (os.getenv("TURSO_DATABASE_URL") or "").strip()
_turso_token = (os.getenv("TURSO_AUTH_TOKEN") or "").strip()
_turso_requested = os.getenv("TURSO_ENABLED", "false").lower() in {
    "1", "true", "yes", "oui"
}
_turso_config_error = None
_turso_enabled = False
# A full embedded replica currently weighs about 180 MB.  That is useful on a
# workstation or a CI runner, but it exceeds the memory available to a
# Streamlit Community Cloud process while the replica is bootstrapped.  Keep
# the cloud-safe, low-memory transport as the default and make replication an
# explicit opt-in for machines with enough RAM.
_turso_access_mode = (os.getenv("TURSO_ACCESS_MODE") or "direct").strip().lower()
_streamlit_cloud = os.path.isdir("/mount/src") or bool(
    os.getenv("STREAMLIT_SHARING_MODE")
)
_force_cloud_direct = os.getenv(
    "STREAMLIT_FORCE_DIRECT_DATABASE", "true"
).strip().lower() in {"1", "true", "yes", "oui"}
if _streamlit_cloud and _force_cloud_direct:
    # Also neutralize an old TURSO_ACCESS_MODE=replica secret that may still be
    # present in the deployed application.
    _turso_access_mode = "direct"

if _turso_requested:
    if not _turso_url or not _turso_token:
        _turso_config_error = "URL ou jeton Turso manquant."
    elif not _turso_url.startswith(("libsql://", "sqlite+libsql://")):
        _turso_config_error = "TURSO_DATABASE_URL doit commencer par libsql://."
    elif _turso_access_mode not in {"replica", "direct"}:
        _turso_config_error = "TURSO_ACCESS_MODE doit valoir replica ou direct."
    else:
        _turso_enabled = True

if _turso_enabled:
    DATABASE_URL = "sqlite://"
else:
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///football.db")

# ``check_same_thread`` is a SQLite-only option; passing it to a PostgreSQL
# driver prevents the application from starting when DATABASE_URL is changed.
_url = make_url(DATABASE_URL)
_is_sqlite = _url.get_backend_name() == "sqlite"
_is_local_sqlite = _url.drivername == "sqlite" and not _turso_enabled
_turso_replica_enabled = _turso_enabled and _turso_access_mode == "replica"
_local_replica_path = os.path.abspath(
    os.getenv("TURSO_LOCAL_DATABASE_PATH", "football-cache-v2.db")
)


def _integer_env(name: str, default: int, minimum: int) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _nonnegative_integer_env(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


if _turso_replica_enabled:
    from database import turso_sync_dbapi

    _pull_interval = _integer_env("TURSO_SYNC_PULL_INTERVAL_SECONDS", 3600, 60)
    _push_retry = _integer_env("TURSO_SYNC_PUSH_RETRY_SECONDS", 300, 60)
    _realtime_interval = _nonnegative_integer_env(
        "TURSO_REALTIME_SYNC_SECONDS", 10
    )
    _strict_push = os.getenv("TURSO_SYNC_STRICT_PUSH", "false").lower() in {
        "1", "true", "yes", "oui"
    }
    engine = create_engine(
        DATABASE_URL,
        module=turso_sync_dbapi,
        creator=lambda: turso_sync_dbapi.connect(
            _local_replica_path,
            _turso_url,
            _turso_token,
            pull_interval_seconds=_pull_interval,
            push_retry_seconds=_push_retry,
            strict_push=_strict_push,
            realtime_interval_seconds=_realtime_interval,
        ),
        poolclass=QueuePool,
        pool_size=5,
        max_overflow=5,
        pool_timeout=30,
        pool_recycle=1800,
        pool_use_lifo=True,
        pool_pre_ping=True,
    )

    @event.listens_for(engine, "checkout")
    def _refresh_local_replica(dbapi_connection, _connection_record, _proxy):
        dbapi_connection.pull_if_due()

elif _turso_enabled:
    from database import turso_http_dbapi

    # ``sqlite://`` selects SingletonThreadPool by default.  Streamlit runs
    # fragments and background synchronisations on several threads; that pool
    # may then close a connection owned by another thread.  A bounded
    # QueuePool gives each checkout exclusive ownership until it is returned.
    engine = create_engine(
        DATABASE_URL,
        module=turso_http_dbapi,
        creator=lambda: turso_http_dbapi.connect(_turso_url, _turso_token),
        poolclass=QueuePool,
        pool_size=5,
        max_overflow=10,
        pool_timeout=30,
        pool_recycle=300,
        pool_use_lifo=True,
        pool_pre_ping=True,
    )
else:
    _connect_args = {"check_same_thread": False, "timeout": 30} if _is_sqlite else {}
    engine = create_engine(
        DATABASE_URL,
        connect_args=_connect_args,
        pool_pre_ping=True,
    )


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
    if _turso_enabled:
        return "turso"
    if _is_local_sqlite:
        return "sqlite_local"
    return _url.get_backend_name()


def persistence_topology() -> str:
    if _turso_replica_enabled:
        return "local_replica"
    if _turso_enabled:
        return "remote_direct"
    return persistence_mode()


def persistence_status() -> dict:
    if not _turso_replica_enabled:
        return {"topology": persistence_topology()}
    status = turso_sync_dbapi.replica_status(_local_replica_path)
    return {"topology": "local_replica", **status}


def persistence_revision() -> int:
    """Return a tiny change marker without reading application data."""
    if _turso_replica_enabled:
        return int(persistence_status().get("revision") or 0)
    try:
        with engine.connect() as conn:
            value = conn.exec_driver_sql(
                "SELECT id FROM update_log ORDER BY id DESC LIMIT 1"
            ).scalar_one_or_none()
        return int(value or 0)
    except Exception:
        # The table may not exist during the very first schema creation.
        return 0


def start_realtime_replica_sync() -> bool:
    if not _turso_replica_enabled or _realtime_interval <= 0:
        return False
    return turso_sync_dbapi.start_realtime_sync(
        _local_replica_path,
        _turso_url,
        _turso_token,
        interval_seconds=_realtime_interval,
        push_retry_seconds=_push_retry,
    )


def persistence_configuration_error() -> str | None:
    return _turso_config_error


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
