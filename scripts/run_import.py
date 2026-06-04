"""Run cautious imports for configured leagues.

Usage:
    python scripts/run_import.py

This will initialize the DB and import the major 5 leagues (France L1, England PL, Spain LaLiga, Italy SerieA, Germany Bundesliga)
for seasons 2016-2026 using `services.import_service.import_leagues_cautious`.
"""
import os
import sys

# Ensure project root is on sys.path so `services` package is importable
ROOT = os.path.dirname(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from services import import_service


if __name__ == '__main__':
    import_service.init_db()
    leagues = [61, 39, 140, 135, 78]
    seasons = list(range(2016, 2027))
    # cautious mode: longer pause to reduce 429s
    import_service.import_leagues_cautious(leagues, seasons=seasons, pause=2.0, max_retries=6)
    print('Import run completed')
