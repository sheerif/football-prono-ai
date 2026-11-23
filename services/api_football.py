import copy
import datetime
import os
import threading
import time
from collections.abc import Callable

import requests
from dotenv import load_dotenv
from requests.exceptions import HTTPError
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from services import api_quota_ledger

load_dotenv()
BASE_URL = "https://v3.football.api-sports.io"
DEFAULT_TIMEOUT = (5, 30)
# Les 429 sont repris par les tâches persistantes au bon renouvellement de
# quota. Les retenter immédiatement gaspillerait plusieurs appels identiques.
RETRY_STATUS_CODES = (500, 502, 503, 504)
_request_lock = threading.RLock()
_daily_blocked_until: datetime.datetime | None = None
_response_cache: dict[tuple, tuple[float, dict]] = {}
_cache_metrics = {
    "cache_hits": 0,
    "cache_misses": 0,
    "requests_avoided": 0,
    "api_data_requests": 0,
    "quota_status_checks": 0,
    "quota_policy_blocks": 0,
    "quota_atomic_reservations": 0,
    "quota_atomic_blocks": 0,
    "quota_ledger_errors": 0,
    "quota_remote_shared_tokens": 0,
    "quota_remote_block_denials": 0,
    "quota_remote_fallbacks": 0,
    "cache_evictions": 0,
}


def cache_metrics_snapshot(*, reset: bool = False) -> dict:
    """Expose process-local cache/quota savings without request payloads."""
    with _request_lock:
        snapshot = {**_cache_metrics, "cache_entries": len(_response_cache)}
        if reset:
            for key in _cache_metrics:
                _cache_metrics[key] = 0
        return snapshot


def _next_midnight_utc() -> datetime.datetime:
    now = datetime.datetime.now(datetime.UTC)
    return (now + datetime.timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def _block_daily_requests() -> None:
    global _daily_blocked_until
    _daily_blocked_until = _next_midnight_utc()


def _daily_requests_blocked() -> bool:
    global _daily_blocked_until
    if _daily_blocked_until is None:
        return False
    if datetime.datetime.now(datetime.UTC) >= _daily_blocked_until:
        _daily_blocked_until = None
        return False
    return True


def _is_daily_quota_error(detail: str) -> bool:
    value = str(detail).casefold()
    return any(
        token in value
        for token in (
            "request limit for the day",
            "request limit for day",
            "daily request",
            "daily quota",
            "quota journalier",
        )
    )


def _cache_ttl_seconds() -> int:
    try:
        return max(0, int(os.getenv("API_FOOTBALL_REQUEST_CACHE_SECONDS", "60")))
    except (TypeError, ValueError):
        return 60



def _request_priority(path: str, params: dict | None) -> str:
    """Classify API calls so the quota policy governs every uncached request."""
    params = params or {}
    if path == "/fixtures" and params.get("live"):
        return "live"
    if path == "/fixtures" and (params.get("from") or params.get("date")):
        try:
            today = datetime.datetime.now(datetime.UTC).date()
            tomorrow = today + datetime.timedelta(days=1)
            start = datetime.date.fromisoformat(str(params.get("from") or params.get("date")))
            end = datetime.date.fromisoformat(str(params.get("to") or params.get("from") or params.get("date")))
            # Reserve quota only for a bounded today/tomorrow window. Broad
            # date-range fixture syncs must not inherit imminent priority.
            if today <= start <= tomorrow and today <= end <= tomorrow and start <= end:
                return "imminent"
        except (TypeError, ValueError):
            pass
    if path in {"/predictions", "/fixtures/lineups", "/fixtures/players"}:
        return "prediction_blocking" if path == "/predictions" else "lineups"
    if path == "/fixtures/statistics":
        return "xg_enrichment"
    if path == "/fixtures":
        return "current_fixtures"
    if path == "/standings":
        return "standings"
    if path == "/teams/statistics":
        return "xg_enrichment"
    if path == "/players":
        return "player_stats"
    if path in {"/fixtures/headtohead"}:
        return "recent_results"
    if path in {"/teams", "/leagues"}:
        return "current_fixtures"
    return "historical_backfill"


def _request_cache_ttl(path: str, params: dict | None) -> int:
    """Use freshness appropriate to each shared dataset, not one TTL for all."""
    params = params or {}
    defaults = {
        "/fixtures": 300,
        "/fixtures/lineups": 120,
        "/fixtures/players": 120,
        "/predictions": 180,
        "/fixtures/statistics": 300,
        "/standings": 900,
        "/teams/statistics": 3600,
        "/players": 3600,
        "/fixtures/headtohead": 21600,
        "/teams": 21600,
        "/leagues": 86400,
    }
    specific_env = None
    default_ttl = defaults.get(path, 60)
    if path == "/fixtures":
        if params.get("live"):
            specific_env = "API_FOOTBALL_CACHE_TTL_LIVE"
            default_ttl = 30
        elif _request_priority(path, params) == "imminent":
            specific_env = "API_FOOTBALL_CACHE_TTL_IMMINENT"
            default_ttl = 180
    path_env = "API_FOOTBALL_CACHE_TTL_" + path.strip("/").replace("/", "_").upper()
    for env_name in (specific_env, path_env, "API_FOOTBALL_REQUEST_CACHE_SECONDS"):
        if not env_name:
            continue
        try:
            configured = os.getenv(env_name)
            if configured is not None:
                return max(0, int(configured))
        except (TypeError, ValueError):
            pass
    return default_ttl


def _cache_key(api_key: str, path: str, params) -> tuple:
    normalized = tuple(sorted((str(key), str(value)) for key, value in (params or {}).items()))
    return hash(api_key), str(path), normalized


def _cached_response(key: tuple):
    cached = _response_cache.get(key)
    if not cached:
        return None
    expires_at, payload = cached
    if time.monotonic() >= expires_at:
        _response_cache.pop(key, None)
        return None
    return copy.deepcopy(payload)


def _remember_response(key: tuple, payload: dict, ttl: int | None = None) -> None:
    ttl = _cache_ttl_seconds() if ttl is None else max(0, int(ttl))
    if ttl <= 0:
        return
    now = time.monotonic()
    expired = [item for item, value in _response_cache.items() if value[0] <= now]
    for item in expired:
        _response_cache.pop(item, None)
    if len(_response_cache) >= 2048:
        oldest = min(_response_cache, key=lambda item: _response_cache[item][0])
        _response_cache.pop(oldest, None)
        _cache_metrics["cache_evictions"] += 1
    _response_cache[key] = (now + ttl, copy.deepcopy(payload))


def _retry_count() -> int:
    try:
        return max(0, int(os.getenv("API_FOOTBALL_HTTP_RETRIES", "3")))
    except ValueError:
        return 3


def _build_session(max_retries: int) -> requests.Session:
    retry = Retry(
        total=max_retries,
        connect=max_retries,
        read=max_retries,
        status=max_retries,
        allowed_methods=frozenset({"GET"}),
        status_forcelist=RETRY_STATUS_CODES,
        backoff_factor=0.5,
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("https://", adapter)
    return session


class ApiFootballClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        session: requests.Session | None = None,
        response_archiver: Callable | None = None,
    ):
        self.base = BASE_URL
        self.api_key = (
            api_key
            if api_key is not None
            else os.getenv("API_FOOTBALL_KEY") or ""
        ).strip()
        self.headers = {"x-apisports-key": self.api_key}
        self.session = session or _build_session(_retry_count())
        self.response_archiver = response_archiver
        self.last_rate_limit = {}
        self.last_quota_status_error = ""
        self.last_quota_status_checked_at = None
        self.request_count = 0

    @staticmethod
    def _error_detail(payload) -> str:
        errors = payload.get("errors") if isinstance(payload, dict) else None
        if not errors:
            return ""
        if isinstance(errors, dict):
            return "; ".join(f"{key}: {value}" for key, value in errors.items())
        return str(errors)

    def _get(self, path, params=None):
        if not self.api_key:
            raise RuntimeError(
                "Clé API_FOOTBALL_KEY manquante. Ajoutez-la dans .env ou dans les secrets Streamlit avant de lancer une synchronisation."
            )
        with _request_lock:
            key = _cache_key(self.api_key, path, params)
            # Status must always be fresh; cached football data avoids a real
            # download and therefore does not need a quota preflight.
            cached = None if path == "/status" else _cached_response(key)
            if cached is not None:
                _cache_metrics["cache_hits"] += 1
                _cache_metrics["requests_avoided"] += 1
                return cached
            _cache_metrics["cache_misses"] += 1
            # The daily circuit breaker blocks network calls only. A still-valid
            # cached response remains usable after the quota has been exhausted.
            if path != "/status" and _daily_requests_blocked():
                _cache_metrics["quota_policy_blocks"] += 1
                raise RuntimeError(
                    "Quota API journalier déjà atteint : appel bloqué localement "
                    "jusqu'à minuit UTC pour éviter une requête refusée supplémentaire."
                )
            if path == "/status":
                _cache_metrics["quota_status_checks"] += 1
            priority = _request_priority(path, params)
            quota_snapshot = None
            if path != "/status":
                try:
                    quota_snapshot = self._preflight_quota()
                except RuntimeError:
                    _cache_metrics["quota_policy_blocks"] += 1
                    raise
                from services import api_quota_policy
                decision = api_quota_policy.can_spend(quota_snapshot, priority)
                if not decision["allowed"]:
                    _cache_metrics["quota_policy_blocks"] += 1
                    raise RuntimeError(
                        f"Appel API différé ({priority}) : {decision['reason']}; "
                        f"réserve protégée={decision.get('reserve', 0)}."
                    )
                try:
                    reservation = api_quota_ledger.reserve_call(
                        quota_snapshot, priority, cost=1
                    )
                except Exception as exc:
                    _cache_metrics["quota_ledger_errors"] += 1
                    raise RuntimeError(
                        "Réservation atomique du quota API indisponible ; "
                        "appel bloqué par sécurité."
                    ) from exc
                if reservation.get("remote_shared"):
                    _cache_metrics["quota_remote_shared_tokens"] += 1
                elif (
                    reservation.get("remote_enabled") is True
                    and not reservation.get("allowed")
                    and str(reservation.get("reason", "")).startswith("remote_")
                ):
                    _cache_metrics["quota_remote_block_denials"] += 1
                elif reservation.get("remote_enabled") is False:
                    _cache_metrics["quota_remote_fallbacks"] += 1
                if not reservation.get("allowed"):
                    _cache_metrics["quota_atomic_blocks"] += 1
                    raise RuntimeError(
                        f"Appel API différé ({priority}) : "
                        f"{reservation.get('reason', 'budget atomique épuisé')}; "
                        f"quota restant atomique={reservation.get('remaining')}."
                    )
                _cache_metrics["quota_atomic_reservations"] += 1
            payload = self._get_unlocked(
                path,
                params,
                count_request=path != "/status",
            )
            if path != "/status":
                _remember_response(key, payload, _request_cache_ttl(path, params))
            return payload

    def _preflight_quota(self) -> dict:
        """Refresh quota immediately before every uncached data request."""
        _cache_metrics["quota_status_checks"] += 1
        payload = self._get_unlocked(
            "/status",
            count_request=False,
            archive_response=False,
            allow_api_errors=True,
        )
        self.last_quota_status_checked_at = (
            self.last_rate_limit.get("checked_at")
        )
        detail = self._error_detail(payload)
        if detail:
            self.last_quota_status_error = detail
            if _is_daily_quota_error(detail):
                _block_daily_requests()
            raise RuntimeError(f"API-Football quota status: {detail}")
        self.last_quota_status_error = ""

        response = payload.get("response") or {}
        if isinstance(response, list):
            response = response[0] if response else {}
        requests = response.get("requests") or {}
        try:
            current = int(requests.get("current"))
            daily_limit = int(requests.get("limit_day"))
        except (TypeError, ValueError):
            current = daily_limit = None

        headers = dict(self.last_rate_limit or {})
        status_daily_limit = daily_limit
        try:
            header_daily_limit = int(headers.get("daily_limit"))
        except (TypeError, ValueError):
            header_daily_limit = None
        daily_limit = header_daily_limit or status_daily_limit
        try:
            daily_remaining = int(headers.get("daily_remaining"))
        except (TypeError, ValueError):
            daily_remaining = None
        try:
            minute_remaining = int(headers.get("minute_remaining"))
        except (TypeError, ValueError):
            minute_remaining = None

        if (
            daily_remaining is not None
            and daily_remaining <= 0
        ) or (
            current is not None
            and daily_limit is not None
            and daily_limit > 0
            and current >= daily_limit
        ):
            _block_daily_requests()
            self.last_quota_status_error = "Quota API journalier épuisé."
            raise RuntimeError(
                "Quota API journalier épuisé : aucun téléchargement lancé."
            )
        if minute_remaining is not None and minute_remaining <= 0:
            retry_after = headers.get("retry_after") or "60"
            self.last_quota_status_error = "Quota API par minute atteint."
            raise RuntimeError(
                "Quota API par minute atteint : aucun téléchargement lancé. "
                f"Réessayez dans {retry_after} seconde(s)."
            )
        # Fallback to /status response counters when rate-limit headers are
        # absent. If both sources are unknown, preserve an unknown snapshot:
        # the policy blocks secondary work but still permits urgent predictions.
        if daily_remaining is None and current is not None and daily_limit is not None and daily_limit > 0:
            daily_remaining = max(0, daily_limit - current)
        if daily_remaining is not None:
            headers["daily_remaining"] = daily_remaining
        if daily_limit is not None:
            headers["daily_limit"] = daily_limit
        if daily_remaining is None or daily_limit is None:
            self.last_quota_status_error = "Réponse /status sans compteur de quota; priorité critique uniquement."
        from services import api_quota_policy
        return api_quota_policy.quota_snapshot(headers)

    def _get_unlocked(
        self,
        path,
        params=None,
        *,
        count_request=True,
        archive_response=True,
        allow_api_errors=False,
    ):
        url = f"{self.base}{path}"
        if count_request:
            self.request_count += 1
            _cache_metrics["api_data_requests"] += 1
        resp = self.session.get(
            url,
            headers=self.headers,
            params=params,
            timeout=DEFAULT_TIMEOUT,
        )
        response_headers = getattr(resp, "headers", {})
        if hasattr(response_headers, "get"):
            self.last_rate_limit = {
                "daily_limit": response_headers.get("x-ratelimit-requests-limit"),
                "daily_remaining": response_headers.get("x-ratelimit-requests-remaining"),
                "minute_limit": response_headers.get("X-RateLimit-Limit"),
                "minute_remaining": response_headers.get("X-RateLimit-Remaining"),
                "retry_after": response_headers.get("Retry-After"),
                "checked_at": datetime.datetime.now(datetime.UTC).replace(
                    tzinfo=None
                ).isoformat(),
            }
            try:
                daily_remaining = int(self.last_rate_limit["daily_remaining"])
            except (TypeError, ValueError):
                daily_remaining = None
            if daily_remaining is not None and daily_remaining <= 0:
                _block_daily_requests()
        try:
            payload = resp.json()
        except ValueError as json_exc:
            payload = {
                "_archive_error": "Réponse JSON invalide",
                "_raw_text": str(getattr(resp, "text", "")),
            }
            if archive_response:
                self._archive(path, params, payload, response_headers, resp)
            raise RuntimeError(
                "API-Football a renvoyé une réponse JSON invalide."
            ) from json_exc

        # Une réponse reçue doit être durable avant toute interprétation métier,
        # y compris lorsqu'API-Football renvoie une erreur ou un quota épuisé.
        if archive_response:
            self._archive(path, params, payload, response_headers, resp)
        if path == "/status":
            self.last_quota_status_checked_at = self.last_rate_limit.get(
                "checked_at"
            )
            self.last_quota_status_error = self._error_detail(payload)

        try:
            resp.raise_for_status()
        except HTTPError as exc:
            detail = self._error_detail(payload) or str(exc)
            if _is_daily_quota_error(detail):
                _block_daily_requests()
            if path == "/status" or "quota" in detail.casefold() or "rate limit" in detail.casefold():
                self.last_quota_status_error = detail
                self.last_quota_status_checked_at = self.last_rate_limit.get(
                    "checked_at"
                )
            if allow_api_errors:
                return payload
            if getattr(resp, "status_code", None) != 429:
                raise
            raise RuntimeError(f"API-Football quota 429 : {detail}") from exc
        detail = self._error_detail(payload)
        if detail:
            if _is_daily_quota_error(detail):
                _block_daily_requests()
            if "quota" in detail.casefold() or "rate limit" in detail.casefold():
                self.last_quota_status_error = detail
                self.last_quota_status_checked_at = self.last_rate_limit.get(
                    "checked_at"
                )
            if allow_api_errors:
                return payload
            raise RuntimeError(f"API-Football a refusé la requête : {detail}")
        return payload

    def _archive(self, path, params, payload, response_headers, response) -> None:
        if self.response_archiver is None:
            return
        self.response_archiver(
            endpoint=str(path),
            params=dict(params or {}),
            payload=payload,
            response_headers=response_headers,
            http_status=getattr(response, "status_code", None),
        )

    def get_leagues(self, country=None):
        params = {"country": country} if country else None
        return self._get("/leagues", params)

    def get_status(self):
        """Retourne l'abonnement et la consommation courante du compte."""
        return self._get("/status")

    def get_teams(self, league_id, season):
        return self._get("/teams", {"league": league_id, "season": season})

    def get_fixtures(self, league_id, season, from_date=None, to_date=None):
        params = {"league": league_id, "season": season}
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date
        return self._get("/fixtures", params)

    def get_fixture(self, fixture_id):
        return self._get("/fixtures", {"id": fixture_id})

    def get_fixture_statistics(self, fixture_id):
        return self._get("/fixtures/statistics", {"fixture": int(fixture_id)})

    def get_headtohead(self, h2h):
        return self._get("/fixtures/headtohead", {"h2h": h2h})

    def get_standings(self, league_id, season):
        return self._get("/standings", {"league": league_id, "season": season})

    def get_team_statistics(self, team_id, league_id, season):
        return self._get("/teams/statistics", {"team": team_id, "league": league_id, "season": season})

    def get_players(self, season, league_id=None, team_id=None, player_id=None, search=None, page=1):
        params = {"season": int(season), "page": int(page)}
        if league_id is not None:
            params["league"] = int(league_id)
        if team_id is not None:
            params["team"] = int(team_id)
        if player_id is not None:
            params["id"] = int(player_id)
        if search:
            params["search"] = str(search)
        return self._get("/players", params)

    def get_fixture_players(self, fixture_id):
        return self._get("/fixtures/players", {"fixture": int(fixture_id)})

    def get_fixture_lineups(self, fixture_id):
        return self._get("/fixtures/lineups", {"fixture": int(fixture_id)})

    def get_predictions(self, fixture_id):
        return self._get("/predictions", {"fixture": fixture_id})
