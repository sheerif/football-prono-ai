import unittest
from unittest.mock import Mock, patch
from requests.exceptions import HTTPError

from services.api_football import (
    DEFAULT_TIMEOUT,
    RETRY_STATUS_CODES,
    ApiFootballClient,
    _build_session,
)


class ApiFootballClientTests(unittest.TestCase):
    def test_missing_key_fails_before_network_access(self):
        session = Mock()
        client = ApiFootballClient(api_key="", session=session)
        with self.assertRaisesRegex(RuntimeError, "manquante"):
            client.get_leagues()
        session.get.assert_not_called()

    def test_get_uses_session_headers_params_and_bounded_timeout(self):
        response = Mock()
        response.json.return_value = {"response": []}
        session = Mock()
        session.get.return_value = response
        client = ApiFootballClient(api_key="test-key", session=session)

        payload = client.get_teams(61, 2026)

        self.assertEqual(payload, {"response": []})
        self.assertEqual(client.request_count, 1)
        session.get.assert_called_once_with(
            "https://v3.football.api-sports.io/teams",
            headers={"x-apisports-key": "test-key"},
            params={"league": 61, "season": 2026},
            timeout=DEFAULT_TIMEOUT,
        )
        response.raise_for_status.assert_called_once_with()

    def test_api_payload_errors_are_not_treated_as_empty_data(self):
        response = Mock()
        response.json.return_value = {"errors": {"rateLimit": "quota reached"}}
        session = Mock()
        session.get.return_value = response
        client = ApiFootballClient(api_key="test-key", session=session)

        with self.assertRaisesRegex(RuntimeError, "quota reached"):
            client.get_leagues()

    def test_status_endpoint_is_available_for_quota_verification(self):
        client = ApiFootballClient(api_key="test")
        with patch.object(client, "_get", return_value={"response": {}}) as get:
            client.get_status()
        get.assert_called_once_with("/status")

    def test_http_429_preserves_minute_quota_detail_and_headers(self):
        response = Mock()
        response.status_code = 429
        response.headers = {
            "x-ratelimit-requests-remaining": "98",
            "X-RateLimit-Remaining": "0",
            "Retry-After": "60",
        }
        response.json.return_value = {
            "errors": {"rateLimit": "Too many requests per minute"}
        }
        response.raise_for_status.side_effect = HTTPError(
            "429 Client Error", response=response
        )
        session = Mock()
        session.get.return_value = response
        client = ApiFootballClient(api_key="test-key", session=session)

        with self.assertRaisesRegex(RuntimeError, "per minute"):
            client.get_leagues()

        self.assertEqual(client.last_rate_limit["daily_remaining"], "98")
        self.assertEqual(client.last_rate_limit["minute_remaining"], "0")

    def test_invalid_json_has_a_clear_error(self):
        response = Mock()
        response.json.side_effect = ValueError("invalid")
        session = Mock()
        session.get.return_value = response
        client = ApiFootballClient(api_key="test-key", session=session)

        with self.assertRaisesRegex(RuntimeError, "JSON invalide"):
            client.get_leagues()

    def test_session_retries_only_get_on_transient_statuses(self):
        session = _build_session(4)
        retry = session.get_adapter("https://").max_retries
        self.assertEqual(retry.total, 4)
        self.assertEqual(retry.allowed_methods, frozenset({"GET"}))
        self.assertEqual(set(retry.status_forcelist), set(RETRY_STATUS_CODES))
        self.assertNotIn(429, retry.status_forcelist)
        self.assertTrue(retry.respect_retry_after_header)


if __name__ == "__main__":
    unittest.main()
