import os
import unittest
from unittest.mock import patch

from components import auth


class AuthenticationSecurityTests(unittest.TestCase):
    def test_credentials_are_required(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(auth.st, "secrets", {}),
        ):
            with self.assertRaises(auth.AuthConfigurationError):
                auth._credentials()

    def test_known_defaults_are_rejected(self):
        with (
            patch.dict(
                os.environ,
                {"APP_USERNAME": "admin", "APP_PASSWORD": "admin"},
                clear=True,
            ),
            patch.object(auth.st, "secrets", {}),
        ):
            with self.assertRaises(auth.AuthConfigurationError):
                auth._credentials()

    def test_legacy_url_token_cannot_restore_a_session(self):
        state = {}
        query = {
            "prono_user": "someone",
            "prono_auth": "reusable-token",
            "prono_expires": "4102444800",
            "page": "dashboard",
        }
        with (
            patch.object(auth.st, "session_state", state),
            patch.object(auth.st, "query_params", query),
        ):
            self.assertFalse(auth.is_authenticated())
        self.assertEqual(query, {"page": "dashboard"})

    def test_active_server_session_is_accepted(self):
        state = {"authenticated": True, "auth_user": "operator"}
        query = {}
        with (
            patch.object(auth.st, "session_state", state),
            patch.object(auth.st, "query_params", query),
        ):
            self.assertTrue(auth.is_authenticated())

    def test_repeated_failures_trigger_a_temporary_lockout(self):
        state = {}
        with patch.object(auth.st, "session_state", state):
            for _ in range(auth.MAX_LOGIN_ATTEMPTS):
                auth._record_failed_attempt()
            self.assertGreater(auth._lockout_seconds_remaining(), 0)


if __name__ == "__main__":
    unittest.main()
