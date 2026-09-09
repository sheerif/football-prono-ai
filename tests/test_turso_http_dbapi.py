import unittest
from unittest.mock import Mock, patch

from database import turso_http_dbapi


class _Result:
    def __init__(self, columns=(), rows=(), rows_affected=0, last_insert_rowid=None):
        self.columns = tuple(columns)
        self.rows = list(rows)
        self.rows_affected = rows_affected
        self.last_insert_rowid = last_insert_rowid


class TursoHttpDbapiTests(unittest.TestCase):
    def test_remote_url_is_converted_to_https_without_exposing_token(self):
        client = Mock()
        client.execute.return_value = _Result(columns=("count",), rows=[(22_143,)])
        with patch.object(
            turso_http_dbapi.libsql_client,
            "create_client_sync",
            return_value=client,
        ) as create_client:
            connection = turso_http_dbapi.connect(
                "libsql://football-prono.example.turso.io",
                "secret-token",
            )
            cursor = connection.execute("SELECT COUNT(*) FROM matches")

        self.assertEqual(cursor.fetchone(), (22_143,))
        self.assertEqual(cursor.description[0][0], "count")
        create_client.assert_called_once_with(
            "https://football-prono.example.turso.io",
            auth_token="secret-token",
        )

    def test_executemany_uses_one_http_batch(self):
        client = Mock()
        client.batch.return_value = [
            _Result(rows_affected=1, last_insert_rowid=1),
            _Result(rows_affected=1, last_insert_rowid=2),
        ]
        with patch.object(
            turso_http_dbapi.libsql_client,
            "create_client_sync",
            return_value=client,
        ):
            connection = turso_http_dbapi.connect(
                "libsql://football-prono.example.turso.io",
                "secret-token",
            )
            cursor = connection.executemany(
                "INSERT INTO sample(id) VALUES (?)",
                [(1,), (2,)],
            )

        self.assertEqual(cursor.rowcount, 2)
        self.assertEqual(cursor.lastrowid, 2)
        client.batch.assert_called_once()


if __name__ == "__main__":
    unittest.main()
