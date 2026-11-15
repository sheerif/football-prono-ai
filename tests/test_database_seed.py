import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy.engine import make_url

from database import database as database_runtime


class DatabaseSeedTests(unittest.TestCase):
    def test_cloud_seed_populates_an_empty_local_database(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "football.db"
            with (
                patch.object(database_runtime, "_streamlit_cloud", True),
                patch.object(
                    database_runtime,
                    "_url",
                    make_url(f"sqlite:///{database_path}"),
                ),
            ):
                database_runtime._seed_cloud_database()

            connection = sqlite3.connect(database_path)
            try:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM matches").fetchone()[0],
                    22299,
                )
            finally:
                connection.close()

    def test_cloud_seed_preserves_an_existing_local_database(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "football.db"
            connection = sqlite3.connect(database_path)
            connection.execute("CREATE TABLE marker (value TEXT)")
            connection.execute("INSERT INTO marker VALUES ('keep')")
            connection.commit()
            connection.close()

            with (
                patch.object(database_runtime, "_streamlit_cloud", True),
                patch.object(
                    database_runtime,
                    "_url",
                    make_url(f"sqlite:///{database_path}"),
                ),
            ):
                database_runtime._seed_cloud_database()

            connection = sqlite3.connect(database_path)
            try:
                self.assertEqual(
                    connection.execute("SELECT value FROM marker").fetchone()[0],
                    "keep",
                )
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
