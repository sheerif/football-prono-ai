import os
import requests
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("API_FOOTBALL_KEY")
BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY}

class ApiFootballClient:
    def __init__(self):
        self.base = BASE_URL
        self.headers = HEADERS

    def _get(self, path, params=None):
        url = f"{self.base}{path}"
        resp = requests.get(url, headers=self.headers, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

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

    def get_headtohead(self, h2h):
        return self._get("/fixtures/headtohead", {"h2h": h2h})

    def get_standings(self, league_id, season):
        return self._get("/standings", {"league": league_id, "season": season})

    def get_team_statistics(self, team_id, league_id, season):
        return self._get("/teams/statistics", {"team": team_id, "league": league_id, "season": season})

    def get_predictions(self, fixture_id):
        return self._get("/predictions", {"fixture": fixture_id})
