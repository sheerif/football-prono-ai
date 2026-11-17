#!/usr/bin/env python3
"""Exécute le backtest chronologique et écrit son résultat JSON sur stdout."""

import argparse
import json
import sys
from pathlib import Path

# Permet l'exécution directe ``python scripts/run_backtest.py`` depuis la
# racine du dépôt, utilisée par la documentation et l'automatisation.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services import analysis_store, backtest_service


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-season", type=int, default=2024)
    parser.add_argument("--min-prior-matches", type=int, default=30)
    parser.add_argument("--production-pipeline", action="store_true")
    parser.add_argument("--production-max-matches", type=int, default=500)
    parser.add_argument(
        "--persisted-predictions",
        action="store_true",
        help="Évalue les prédictions internes réellement enregistrées avant le coup d'envoi.",
    )
    args = parser.parse_args()
    if args.persisted_predictions:
        # Crée les tables d'historique et importe les anciens snapshots de façon idempotente.
        analysis_store.ensure_prediction_history()
        result = backtest_service.evaluate_persisted_predictions()
    else:
        result = backtest_service.run_from_database(
            start_season=args.start_season,
            min_prior_matches=args.min_prior_matches,
            include_production_pipeline=args.production_pipeline,
            production_max_matches=args.production_max_matches,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
