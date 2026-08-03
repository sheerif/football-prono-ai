"""Backtest chronologique des formules 1/N/2.

Le contexte d'un match ne contient que les rencontres terminées dont la date
est strictement antérieure au coup d'envoi. Une prédiction API n'est utilisée
que si sa date de collecte est elle aussi strictement antérieure.
"""

from __future__ import annotations

import math
import hashlib
from collections import defaultdict, deque

import pandas as pd
from sqlalchemy import text

from database.database import engine
from services import (
    cross_insight_service,
    prediction_helpers,
    prediction_service,
    ranking_service,
)


PROBABILITY_KEYS = (
    "home_probability",
    "draw_probability",
    "away_probability",
)


def old_formula(home_strength: float, away_strength: float) -> dict:
    """Reproduit exactement la formule présente sur ``main`` avant la PR."""
    draw = 0.2 * min(float(home_strength), float(away_strength))
    values = prediction_service.normalize_probs(
        [float(home_strength), draw, float(away_strength)]
    )
    return dict(zip(PROBABILITY_KEYS, values, strict=True))


def _outcome(row) -> int:
    home_goals = int(row["home_goals"])
    away_goals = int(row["away_goals"])
    return 0 if home_goals > away_goals else 1 if home_goals == away_goals else 2


def _metrics(records: list[tuple[list[float], int]]) -> dict:
    if not records:
        return {
            "matches": 0,
            "accuracy_1n2": None,
            "brier_score": None,
            "log_loss": None,
            "expected_calibration_error": None,
            "calibration_curve": [],
            "accuracy_by_outcome": {
                "home": None,
                "draw": None,
                "away": None,
            },
            "draws": 0,
            "draw_recall": None,
            "draw_precision": None,
            "draw_brier": None,
            "mean_draw_probability_on_draws": None,
        }
    correct = 0
    brier = 0.0
    log_loss = 0.0
    actual_draws = 0
    predicted_draws = 0
    correct_draws = 0
    draw_brier = 0.0
    draw_probability_on_draws = 0.0
    calibration = []
    outcome_totals = [0, 0, 0]
    outcome_correct = [0, 0, 0]
    for probabilities, outcome in records:
        probs = [max(0.0, min(1.0, float(value) / 100)) for value in probabilities]
        predicted = max(range(3), key=probs.__getitem__)
        correct += predicted == outcome
        outcome_totals[outcome] += 1
        outcome_correct[outcome] += predicted == outcome
        calibration.append((max(probs), int(predicted == outcome)))
        brier += sum((probs[index] - (index == outcome)) ** 2 for index in range(3))
        log_loss -= math.log(max(1e-15, probs[outcome]))
        is_draw = outcome == 1
        predicts_draw = predicted == 1
        actual_draws += is_draw
        predicted_draws += predicts_draw
        correct_draws += is_draw and predicts_draw
        draw_brier += (probs[1] - is_draw) ** 2
        if is_draw:
            draw_probability_on_draws += probs[1]
    count = len(records)
    calibration_curve = []
    ece = 0.0
    for bin_index in range(10):
        lower = bin_index / 10
        upper = (bin_index + 1) / 10
        bucket = [
            item
            for item in calibration
            if lower <= item[0] <= upper
            if bin_index == 9 or item[0] < upper
        ]
        if not bucket:
            continue
        mean_confidence = sum(item[0] for item in bucket) / len(bucket)
        observed_accuracy = sum(item[1] for item in bucket) / len(bucket)
        ece += len(bucket) / count * abs(observed_accuracy - mean_confidence)
        calibration_curve.append(
            {
                "bin": f"{bin_index * 10}-{(bin_index + 1) * 10}",
                "matches": len(bucket),
                "mean_confidence": round(mean_confidence, 6),
                "observed_accuracy": round(observed_accuracy, 6),
            }
        )
    accuracy_by_outcome = {
        label: (
            round(outcome_correct[index] / outcome_totals[index], 6)
            if outcome_totals[index]
            else None
        )
        for index, label in enumerate(("home", "draw", "away"))
    }
    return {
        "matches": count,
        "accuracy_1n2": round(correct / count, 6),
        "brier_score": round(brier / count, 6),
        "log_loss": round(log_loss / count, 6),
        "expected_calibration_error": round(ece, 6),
        "calibration_curve": calibration_curve,
        "accuracy_by_outcome": accuracy_by_outcome,
        "accuracy_home": accuracy_by_outcome["home"],
        "accuracy_draw": accuracy_by_outcome["draw"],
        "accuracy_away": accuracy_by_outcome["away"],
        "draws": actual_draws,
        "draw_recall": round(correct_draws / actual_draws, 6) if actual_draws else None,
        "draw_precision": round(correct_draws / predicted_draws, 6) if predicted_draws else None,
        "draw_brier": round(draw_brier / count, 6),
        "mean_draw_probability_on_draws": (
            round(draw_probability_on_draws / actual_draws, 6)
            if actual_draws
            else None
        ),
    }


def _accuracy_by_league(records: list[dict]) -> list[dict]:
    grouped: dict[int, list[bool]] = defaultdict(list)
    for record in records:
        grouped[int(record["league_id"])].append(bool(record["correct"]))
    return [
        {
            "league_id": league_id,
            "matches": len(values),
            "accuracy": round(sum(values) / len(values), 6),
        }
        for league_id, values in sorted(grouped.items())
    ]


def _accuracy_by_ranking_score(records: list[dict]) -> list[dict]:
    """Construit le tableau de validation monotone demandé pour l'indice."""
    rows = []
    for lower, upper in ((90, 100), (80, 90), (70, 80), (60, 70), (50, 60)):
        values = [
            bool(record["correct"])
            for record in records
            if lower <= float(record["ranking_score"])
            and (
                float(record["ranking_score"]) <= upper
                if upper == 100
                else float(record["ranking_score"]) < upper
            )
        ]
        rows.append(
            {
                "ranking_score": f"{lower}-{upper}",
                "matches": len(values),
                "real_accuracy": (
                    round(sum(values) / len(values), 6) if values else None
                ),
            }
        )
    return rows


def load_dataset() -> pd.DataFrame:
    return pd.read_sql(
        text(
            """
            SELECT m.*,
                   p.advice AS api_advice,
                   p.winner AS api_winner,
                   p.home_probability AS api_home_probability,
                   p.draw_probability AS api_draw_probability,
                   p.away_probability AS api_away_probability,
                   p.total_home AS api_total_home,
                   p.total_away AS api_total_away,
                   p.raw_json AS api_raw_json,
                   p.updated_at AS api_updated_at
            FROM matches m
            LEFT JOIN fixture_api_predictions p ON p.fixture_id = m.fixture_id
            WHERE m.home_goals IS NOT NULL AND m.away_goals IS NOT NULL
            ORDER BY m.league_id, m.date, m.fixture_id
            """
        ),
        engine,
    )


def run(
    matches: pd.DataFrame,
    *,
    start_season: int | None = None,
    min_prior_matches: int = 30,
) -> dict:
    """Exécute un backtest walk-forward déterministe sur un DataFrame complet."""
    frame = matches.copy()
    frame["_kickoff"] = pd.to_datetime(frame["date"], errors="coerce", utc=True)
    frame = frame.dropna(subset=["_kickoff", "home_goals", "away_goals"])
    frame = frame.sort_values(["league_id", "_kickoff", "fixture_id"])
    candidates = frame
    if start_season is not None:
        candidates = frame[frame["season"].astype(int) >= int(start_season)]

    old_records: list[tuple[list[float], int]] = []
    new_records: list[tuple[list[float], int]] = []
    api_records: list[tuple[list[float], int]] = []
    old_api_subset: list[tuple[list[float], int]] = []
    new_api_subset: list[tuple[list[float], int]] = []
    ranking_records: list[dict] = []
    post_kickoff_api_excluded = 0

    candidate_ids = set(candidates["fixture_id"].astype(int))
    for _league_id, league in frame.groupby("league_id", sort=True):
        team_stats = defaultdict(
            lambda: {
                "played": 0,
                "goals_for": 0,
                "goals_against": 0,
                "recent": deque(maxlen=8),
                "all_results": [],
            }
        )
        prior_matches = 0
        prior_draws = 0
        # Aucune rencontre ayant exactement le même horaire ne peut entrer
        # dans le contexte d'une autre : on prédit tout le groupe avant update.
        for kickoff, kickoff_matches in league.groupby("_kickoff", sort=True):
            if prior_matches >= int(min_prior_matches):
                draw_rate = max(
                    0.15,
                    min(0.35, prior_draws / prior_matches if prior_matches >= 30 else 0.25),
                )
                for _, match in kickoff_matches.iterrows():
                    if int(match["fixture_id"]) not in candidate_ids:
                        continue
                    home_team = int(match["home_team_id"])
                    away_team = int(match["away_team_id"])
                    home = team_stats[home_team]
                    away = team_stats[away_team]
                    home_form = prediction_helpers.form_score(list(home["recent"]))
                    away_form = prediction_helpers.form_score(list(away["recent"]))
                    home_played = max(1, home["played"])
                    away_played = max(1, away["played"])
                    home_attack = home["goals_for"] / home_played
                    away_attack = away["goals_for"] / away_played
                    home_defense = home["goals_against"] / home_played
                    away_defense = away["goals_against"] / away_played
                    home_strength = (
                        0.55 * home_form
                        + 0.25 * max(0.05, home_attack - away_defense + 1)
                        + 0.20 * 0.65
                    )
                    away_strength = (
                        0.55 * away_form
                        + 0.25 * max(0.05, away_attack - home_defense + 1)
                        + 0.20 * 0.45
                    )
                    new = prediction_service.predict_simple(
                        home_strength, away_strength, draw_factor=draw_rate
                    )
                    old = old_formula(home_strength, away_strength)
                    outcome = _outcome(match)
                    old_values = [old[key] for key in PROBABILITY_KEYS]
                    new_values = [new[key] for key in PROBABILITY_KEYS]
                    old_records.append((old_values, outcome))
                    new_records.append((new_values, outcome))
                    stability_windows = [new]
                    for window_size in (5, 10):
                        home_window_form = prediction_helpers.form_score(
                            home["all_results"][-window_size:]
                        )
                        away_window_form = prediction_helpers.form_score(
                            away["all_results"][-window_size:]
                        )
                        window_home_strength = (
                            0.55 * home_window_form
                            + 0.25 * max(0.05, home_attack - away_defense + 1)
                            + 0.20 * 0.65
                        )
                        window_away_strength = (
                            0.55 * away_window_form
                            + 0.25 * max(0.05, away_attack - home_defense + 1)
                            + 0.20 * 0.45
                        )
                        stability_windows.append(
                            prediction_service.predict_simple(
                                window_home_strength,
                                window_away_strength,
                                draw_factor=draw_rate,
                            )
                        )
                    ranking = ranking_service.compute_ranking_score(
                        new,
                        data_quality=ranking_service.compute_data_quality(
                            historical_match_count=prior_matches,
                            statistics_coverage=float(
                                home["played"] > 0 and away["played"] > 0
                            ),
                            lineup_coverage=0.0,
                            freshness=1.0,
                            api_coverage=0.0,
                        ),
                        stability_score=ranking_service.compute_stability(
                            stability_windows
                        ),
                        agreement_score=ranking_service.compute_agreement(None),
                    )
                    ranking_records.append(
                        {
                            "league_id": int(match["league_id"]),
                            "ranking_score": ranking["ranking_score"],
                            "correct": max(
                                range(3), key=new_values.__getitem__
                            )
                            == outcome,
                        }
                    )

                    api_updated_at = pd.to_datetime(
                        match.get("api_updated_at"), errors="coerce", utc=True
                    )
                    has_api = all(pd.notna(match.get(key)) for key in (
                        "api_home_probability", "api_draw_probability", "api_away_probability"
                    ))
                    if has_api and pd.notna(api_updated_at) and api_updated_at >= kickoff:
                        post_kickoff_api_excluded += 1
                        continue
                    if not has_api or pd.isna(api_updated_at):
                        continue
                    api_signal = cross_insight_service._api_signal_from_row(
                        {
                            "fixture_id": match["fixture_id"],
                            "date": match["date"],
                            "advice": match.get("api_advice"),
                            "winner": match.get("api_winner"),
                            "home_probability": match.get("api_home_probability"),
                            "draw_probability": match.get("api_draw_probability"),
                            "away_probability": match.get("api_away_probability"),
                            "total_home": match.get("api_total_home"),
                            "total_away": match.get("api_total_away"),
                            "raw_json": match.get("api_raw_json"),
                            "updated_at": match.get("api_updated_at"),
                        }
                    )
                    combined, refinement = prediction_service.blend_with_api_prediction(
                        new, api_signal
                    )
                    if not refinement.get("applied"):
                        continue
                    api_records.append(([combined[key] for key in PROBABILITY_KEYS], outcome))
                    old_api_subset.append((old_values, outcome))
                    new_api_subset.append((new_values, outcome))

            for _, match in kickoff_matches.iterrows():
                home_team = int(match["home_team_id"])
                away_team = int(match["away_team_id"])
                home_goals = int(match["home_goals"])
                away_goals = int(match["away_goals"])
                for team_id, goals_for, goals_against in (
                    (home_team, home_goals, away_goals),
                    (away_team, away_goals, home_goals),
                ):
                    stats = team_stats[team_id]
                    stats["played"] += 1
                    stats["goals_for"] += goals_for
                    stats["goals_against"] += goals_against
                    stats["recent"].append(
                        "W" if goals_for > goals_against else "D" if goals_for == goals_against else "L"
                    )
                    stats["all_results"].append(
                        "W" if goals_for > goals_against else "D" if goals_for == goals_against else "L"
                    )
                prior_matches += 1
                prior_draws += home_goals == away_goals

    fingerprint_columns = [
        "fixture_id", "league_id", "season", "date", "home_team_id",
        "away_team_id", "home_goals", "away_goals", "api_home_probability",
        "api_draw_probability", "api_away_probability", "api_updated_at",
    ]
    fingerprint_frame = frame[
        [column for column in fingerprint_columns if column in frame.columns]
    ].drop(columns=["_kickoff"], errors="ignore")
    dataset_fingerprint = hashlib.sha256(
        fingerprint_frame.to_json(orient="records", date_format="iso").encode("utf-8")
    ).hexdigest()
    new_metrics = _metrics(new_records)
    new_metrics["accuracy_by_league"] = _accuracy_by_league(ranking_records)
    new_metrics["accuracy_by_ranking_score"] = _accuracy_by_ranking_score(
        ranking_records
    )
    return {
        "configuration": {
            "start_season": start_season,
            "min_prior_matches": int(min_prior_matches),
            "chronology": "context.date < kickoff and api.updated_at < kickoff",
            "dataset_rows": int(len(frame)),
            "dataset_sha256": dataset_fingerprint,
        },
        "old_formula": _metrics(old_records),
        "new_draw_formula": new_metrics,
        "api_comparable_subset": {
            "old_formula": _metrics(old_api_subset),
            "new_draw_formula": _metrics(new_api_subset),
            "combined_api_football": _metrics(api_records),
        },
        "post_kickoff_api_predictions_excluded": post_kickoff_api_excluded,
    }


def run_from_database(**kwargs) -> dict:
    return run(load_dataset(), **kwargs)
