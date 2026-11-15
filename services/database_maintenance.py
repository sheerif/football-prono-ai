"""Low-risk SQLite maintenance after batches of data writes."""

from __future__ import annotations

import threading

from database.database import engine

_maintenance_lock = threading.Lock()
_VACUUM_MIN_FREE_PAGES = 2_048
_VACUUM_FREE_RATIO = 0.05
_INCREMENTAL_VACUUM_PAGES = 1_000


def maintain_database(*, database_engine=engine) -> dict:
    """Refresh planner statistics, checkpoint WAL, and reclaim free pages.

    The full VACUUM threshold avoids blocking writes for tiny gains. In WAL
    mode, PASSIVE checkpoints do not stop readers or writers.
    """
    if database_engine.dialect.name != "sqlite":
        return {"status": "unsupported_database"}
    if not _maintenance_lock.acquire(blocking=False):
        return {"status": "already_running"}

    try:
        with database_engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA optimize")
            connection.commit()
            checkpoint = connection.exec_driver_sql(
                "PRAGMA wal_checkpoint(PASSIVE)"
            ).one()
            connection.commit()
            page_size = int(connection.exec_driver_sql("PRAGMA page_size").scalar())
            page_count = int(connection.exec_driver_sql("PRAGMA page_count").scalar())
            free_pages = int(
                connection.exec_driver_sql("PRAGMA freelist_count").scalar()
            )
            auto_vacuum = int(
                connection.exec_driver_sql("PRAGMA auto_vacuum").scalar()
            )
            connection.commit()

            reclaimed_pages = 0
            vacuumed = False
            if free_pages and auto_vacuum == 2:
                connection.exec_driver_sql(
                    f"PRAGMA incremental_vacuum({_INCREMENTAL_VACUUM_PAGES})"
                )
                connection.commit()
                remaining = int(
                    connection.exec_driver_sql("PRAGMA freelist_count").scalar()
                )
                connection.commit()
                reclaimed_pages = max(0, free_pages - remaining)
            elif free_pages >= max(
                _VACUUM_MIN_FREE_PAGES,
                int(page_count * _VACUUM_FREE_RATIO),
            ):
                # This runs only after a write batch has finished. Readers can
                # continue in WAL mode; new writes wait for the short rebuild.
                connection.exec_driver_sql("VACUUM")
                connection.commit()
                vacuumed = True
                remaining = int(
                    connection.exec_driver_sql("PRAGMA freelist_count").scalar()
                )
                connection.commit()
                reclaimed_pages = max(0, free_pages - remaining)

            final_pages = int(connection.exec_driver_sql("PRAGMA page_count").scalar())
            final_free_pages = int(
                connection.exec_driver_sql("PRAGMA freelist_count").scalar()
            )
            connection.commit()

        return {
            "status": "maintained",
            "page_size": page_size,
            "page_count_before": page_count,
            "free_pages_before": free_pages,
            "page_count_after": final_pages,
            "free_pages_after": final_free_pages,
            "reclaimed_bytes": reclaimed_pages * page_size,
            "vacuumed": vacuumed,
            "checkpoint": tuple(int(value) for value in checkpoint),
        }
    finally:
        _maintenance_lock.release()
