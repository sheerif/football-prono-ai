#!/usr/bin/env python3
"""Exécute le backtest chronologique et écrit son résultat JSON sur stdout."""

import argparse
import json

from services import backtest_service


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-season", type=int, default=2024)
    parser.add_argument("--min-prior-matches", type=int, default=30)
    args = parser.parse_args()
    result = backtest_service.run_from_database(
        start_season=args.start_season,
        min_prior_matches=args.min_prior_matches,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
