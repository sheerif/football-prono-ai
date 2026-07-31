import os

import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

load_dotenv()
BASE_URL = "https://v3.football.api-sports.io"
DEFAULT_TIMEOUT = (5, 30)
RETRY_STATUS_CODES = (429, 500, 502, 503, 504)


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
    ):
        self.base = BASE_URL
        self.api_key = (
            api_key
            if api_key is not None
            else os.getenv("API_FOOTBALL_KEY") or ""
        ).strip()
        self.headers = {"x-apisports-key": self.api_key}
        self.session = session or _build_session(_retry_count())

    def _get(self, path, params=None):
        if not self.api_key:
            raise RuntimeError(
                "Clé API_FOOTBALL_KEY manquante. Ajoutez-la dans .env ou dans les secrets Streamlit avant de lancer une synchronisation."
            )
        url = f"{self.base}{path}"
        resp = self.session.get(
            url,
            headers=self.headers,
            params=params,
            timeout=DEFAULT_TIMEOUT,
        )
        resp.raise_for_status()
        try:
            payload = resp.json()
        except ValueError as exc:
            raise RuntimeError(
                "API-Football a renvoyé une réponse JSON invalide."
            ) from exc
        errors = payload.get("errors") if isinstance(payload, dict) else None
        if errors:
            if isinstance(errors, dict):
                detail = "; ".join(f"{k}: {v}" for k, v in errors.items())
            else:
                detail = str(errors)
            raise RuntimeError(f"API-Football a refusé la requête : {detail}")
        return payload

    def get_leagues(self, country=None):
        params = {"country": country} if country else None
        return self._get("/leagues", params)

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
