import os
import tempfile
import unittest
from unittest.mock import patch

import turso
from sqlalchemy import create_engine, text
from sqlalchemy.pool import QueuePool

from database import turso_sync_dbapi


class _LocalSyncConnection:
    def __init__(self, path):
        self.connection = turso.connect(path)
        self.push_calls = 0
        self.pull_calls = 0
        self.fail_push = False
        self.push_error = "cloud unavailable"
        self.fail_pull = False
        self.pull_changed = False

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def push(self):
        self.push_calls += 1
        if self.fail_push:
            raise RuntimeError(self.push_error)

    def pull(self):
        self.pull_calls += 1
        if self.fail_pull:
            raise RuntimeError("rows read limit reached")
        changed = self.pull_changed
        self.pull_changed = False
        return changed


class TursoSyncDbapiTests(unittest.TestCase):
    def setUp(self):
        turso_sync_dbapi._reset_states_for_tests()
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temporary_directory.name, "replica.db")
        self.connections = []

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _engine(self):
        def fake_connect(*_args, **_kwargs):
            connection = _LocalSyncConnection(self.path)
            self.connections.append(connection)
            return connection

        patcher = patch.object(
            turso_sync_dbapi.turso.sync,
            "connect",
            side_effect=fake_connect,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        return create_engine(
            "sqlite://",
            module=turso_sync_dbapi,
            creator=lambda: turso_sync_dbapi.connect(
                self.path,
                "libsql://example.turso.io",
                "secret",
                pull_interval_seconds=3600,
            ),
            poolclass=QueuePool,
            pool_size=1,
        )

    def test_reads_are_local_and_do_not_push_or_pull_repeatedly(self):
        engine = self._engine()
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE sample(id INTEGER PRIMARY KEY)"))
        raw = self.connections[0]
        initial_pushes = raw.push_calls
        initial_pulls = raw.pull_calls

        for _ in range(5):
            with engine.connect() as connection:
                self.assertEqual(
                    connection.execute(text("SELECT COUNT(*) FROM sample")).scalar_one(),
                    0,
                )

        self.assertEqual(raw.push_calls, initial_pushes)
        self.assertEqual(raw.pull_calls, initial_pulls)
        engine.dispose()

    def test_commit_pushes_useful_writes_to_remote_backup(self):
        engine = self._engine()
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE sample(id INTEGER PRIMARY KEY)"))
        raw = self.connections[0]
        pushes_after_schema = raw.push_calls

        with engine.begin() as connection:
            connection.execute(text("INSERT INTO sample(id) VALUES (1)"))

        self.assertEqual(raw.push_calls, pushes_after_schema + 1)
        self.assertFalse(
            turso_sync_dbapi.replica_status(self.path)["pending_push"]
        )
        engine.dispose()

    def test_failed_cloud_push_keeps_local_data_and_pending_state(self):
        engine = self._engine()
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE sample(id INTEGER PRIMARY KEY)"))
        raw = self.connections[0]
        raw.fail_push = True

        with engine.begin() as connection:
            connection.execute(text("INSERT INTO sample(id) VALUES (1)"))

        with engine.connect() as connection:
            self.assertEqual(
                connection.execute(text("SELECT COUNT(*) FROM sample")).scalar_one(),
                1,
            )
        status = turso_sync_dbapi.replica_status(self.path)
        self.assertTrue(status["pending_push"])
        self.assertIn("cloud unavailable", status["last_error"])
        engine.dispose()

    def test_changed_pull_increments_visible_replica_revision(self):
        engine = self._engine()
        with engine.connect():
            pass
        raw = self.connections[0]
        raw.pull_changed = True
        wrapped = engine.raw_connection().dbapi_connection

        wrapped.pull_if_due(force=True)

        status = turso_sync_dbapi.replica_status(self.path)
        self.assertEqual(status["revision"], 1)
        engine.dispose()

    def test_quota_block_opens_circuit_and_keeps_followup_commits_local(self):
        engine = self._engine()
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE sample(id INTEGER PRIMARY KEY)"))
        raw = self.connections[0]
        raw.fail_push = True
        raw.push_error = (
            'Operation was blocked: SQL read operations are forbidden '
            '(reads are blocked); code: "BLOCKED"'
        )

        with engine.begin() as connection:
            connection.execute(text("INSERT INTO sample(id) VALUES (1)"))
        calls_after_block = raw.push_calls
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO sample(id) VALUES (2)"))

        self.assertEqual(raw.push_calls, calls_after_block)
        with engine.connect() as connection:
            self.assertEqual(
                connection.execute(text("SELECT COUNT(*) FROM sample")).scalar_one(),
                2,
            )
        status = turso_sync_dbapi.replica_status(self.path)
        self.assertTrue(status["pending_push"])
        self.assertTrue(status["cloud_blocked"])
        self.assertGreater(status["cloud_retry_in_seconds"], 0)
        engine.dispose()

    def test_failed_pull_is_throttled_instead_of_retried_on_every_read(self):
        engine = self._engine()
        with engine.connect():
            pass
        raw = self.connections[0]
        raw.fail_pull = True
        wrapped = engine.raw_connection().dbapi_connection

        wrapped.pull_if_due(force=True)
        calls_after_failure = raw.pull_calls
        wrapped.pull_if_due()

        self.assertEqual(raw.pull_calls, calls_after_failure)
        self.assertIn(
            "rows read limit reached",
            turso_sync_dbapi.replica_status(self.path)["last_error"],
        )
        engine.dispose()

    def test_realtime_worker_is_not_started_during_database_connection(self):
        engine = self._engine()
        with engine.connect():
            pass

        status = turso_sync_dbapi.replica_status(self.path)
        self.assertTrue(status["initialized"])
        self.assertFalse(status["realtime_started"])
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
