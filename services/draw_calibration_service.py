"""Calibration conservatrice de la probabilité de match nul.

Modèle logistique figé et versionné, entraîné en walk-forward sur 19 035
rencontres des saisons 2016–2025. Les probabilités domicile/extérieur sont
rééchelonnées proportionnellement : la calibration ne crée pas de masse et la
matrice de scores reste la source de vérité.
"""

from __future__ import annotations

import math
from collections.abc import Mapping


MODEL_VERSION = "draw-logistic-v1-2016-2025"
TRAINING_MATCHES = 19035
TRAINING_DATA_SHA256 = "5f49498058ad33e40273ac08975cfee2f21224f9a30d9e7e1069fa671661fb70"

_FEATURE_MEAN = (
    -1.1076197873752849,
    0.4407786301064129,
    2.723313550143198,
    1.8063270308084312,
    0.15959702651497104,
    0.06984662463882306,
    0.11595578195428106,
)
_FEATURE_SCALE = (
    0.1450854845055192,
    0.34410544658088094,
    0.34866286003154173,
    0.4613896122508411,
    0.11738094103257503,
    0.025580394887637567,
    0.01268315646068541,
)
_COEFFICIENTS = (
    0.12521213517982432,
    -0.14628460003958246,
    0.03710685244779449,
    0.05369528387101121,
    0.05128105020394428,
    0.038083827526329495,
    0.06874823861559781,
)
_INTERCEPT = -1.1298208106470733


def _finite(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _unchanged(score_prediction: Mapping[str, object], reason: str) -> tuple[dict, dict]:
    return dict(score_prediction), {
        "applied": False,
        "model_version": MODEL_VERSION,
        "reason": reason,
    }


def calibrate_score_prediction(
    score_prediction: Mapping[str, object] | None,
) -> tuple[dict, dict]:
    """Calibre le nul et rééchelonne la matrice sans rompre les invariants."""
    if not isinstance(score_prediction, Mapping):
        return {}, {"applied": False, "model_version": MODEL_VERSION, "reason": "score_prediction indisponible"}

    matrix = score_prediction.get("matrix")
    probabilities = score_prediction.get("probabilities")
    if not isinstance(matrix, (list, tuple)) or not matrix or not isinstance(probabilities, Mapping):
        return _unchanged(score_prediction, "matrice ou distribution absente")

    try:
        raw = [
            float(probabilities["home_probability"]),
            float(probabilities["draw_probability"]),
            float(probabilities["away_probability"]),
        ]
    except (KeyError, TypeError, ValueError):
        return _unchanged(score_prediction, "distribution 1/N/2 invalide")
    if (
        not all(math.isfinite(value) and 0.0 <= value <= 100.0 for value in raw)
        or abs(sum(raw) - 100.0) > 0.08
        or raw[1] <= 0.0
        or raw[1] >= 100.0
        or raw[0] + raw[2] <= 0.0
    ):
        return _unchanged(score_prediction, "probabilités hors bornes")

    home_lambda = _finite(score_prediction.get("expected_home_goals"))
    away_lambda = _finite(score_prediction.get("expected_away_goals"))
    if home_lambda is None or away_lambda is None or home_lambda < 0 or away_lambda < 0:
        return _unchanged(score_prediction, "buts projetés indisponibles")

    copied_matrix = [dict(row) for row in matrix if isinstance(row, Mapping)]
    if len(copied_matrix) != len(matrix):
        return _unchanged(score_prediction, "matrice de scores invalide")

    group_totals = [0.0, 0.0, 0.0]
    probability_00 = None
    probability_11 = None
    row_outcomes: list[int] = []
    for row in copied_matrix:
        home_goals = _finite(row.get("Buts domicile"))
        away_goals = _finite(row.get("Buts extérieur"))
        cell_probability = _finite(row.get("Probabilité"))
        if (
            home_goals is None or away_goals is None or cell_probability is None
            or cell_probability < 0
        ):
            return _unchanged(score_prediction, "cellule de score invalide")
        home_goals, away_goals = int(home_goals), int(away_goals)
        outcome = 0 if home_goals > away_goals else 1 if home_goals == away_goals else 2
        row_outcomes.append(outcome)
        group_totals[outcome] += cell_probability
        if home_goals == 0 and away_goals == 0:
            probability_00 = cell_probability / 100.0
        elif home_goals == 1 and away_goals == 1:
            probability_11 = cell_probability / 100.0

    matrix_total = sum(group_totals)
    if (
        abs(matrix_total - 100.0) > 0.08
        or any(value <= 0.0 for value in group_totals)
        or probability_00 is None
        or probability_11 is None
    ):
        return _unchanged(score_prediction, "matrice incomplète ou non normalisée")

    raw_draw = raw[1] / 100.0
    total_goals = home_lambda + away_lambda
    if total_goals <= 0.0:
        return _unchanged(score_prediction, "total de buts projetés nul")

    features = (
        math.log(raw_draw / (1.0 - raw_draw)),
        abs(home_lambda - away_lambda),
        total_goals,
        home_lambda * away_lambda,
        abs(home_lambda - away_lambda) / total_goals,
        probability_00,
        probability_11,
    )
    z_score = _INTERCEPT + sum(
        coefficient * ((value - mean) / scale)
        for coefficient, value, mean, scale in zip(
            _COEFFICIENTS, features, _FEATURE_MEAN, _FEATURE_SCALE, strict=True
        )
    )
    z_score = max(-35.0, min(35.0, z_score))
    calibrated_draw = 1.0 / (1.0 + math.exp(-z_score))
    # Garde-fou contre les contextes très éloignés du jeu d'entraînement.
    calibrated_draw = max(0.05, min(0.45, calibrated_draw))

    decisive_total = raw[0] + raw[2]
    target = [
        (100.0 - calibrated_draw * 100.0) * raw[0] / decisive_total,
        calibrated_draw * 100.0,
        (100.0 - calibrated_draw * 100.0) * raw[2] / decisive_total,
    ]

    for row, outcome in zip(copied_matrix, row_outcomes, strict=True):
        row["Probabilité"] = round(
            float(row["Probabilité"]) * target[outcome] / group_totals[outcome],
            4,
        )

    # Corrige uniquement les écarts d'arrondi ; les trois groupes restent
    # proportionnels aux masses cibles calculées ci-dessus.
    adjusted_total = sum(float(row["Probabilité"]) for row in copied_matrix)
    if adjusted_total <= 0.0:
        return _unchanged(score_prediction, "normalisation de matrice impossible")
    for row in copied_matrix:
        row["Probabilité"] = round(float(row["Probabilité"]) * 100.0 / adjusted_total, 4)

    adjusted_groups = [0.0, 0.0, 0.0]
    for row, outcome in zip(copied_matrix, row_outcomes, strict=True):
        adjusted_groups[outcome] += float(row["Probabilité"])
    normalized = [
        round(value / sum(adjusted_groups) * 100.0, 2)
        for value in adjusted_groups
    ]
    rounding_residual = round(100.0 - sum(normalized), 2)
    largest_group = max(range(3), key=normalized.__getitem__)
    normalized[largest_group] = round(
        normalized[largest_group] + rounding_residual, 2
    )

    result = dict(score_prediction)
    result["matrix"] = copied_matrix
    result["probabilities"] = {
        "home_probability": normalized[0],
        "draw_probability": normalized[1],
        "away_probability": normalized[2],
    }
    metadata = {
        "applied": True,
        "model_version": MODEL_VERSION,
        "training_matches": TRAINING_MATCHES,
        "training_period": "2016-2025",
        "training_data_sha256": TRAINING_DATA_SHA256,
        "raw_draw_probability": round(raw[1], 2),
        "calibrated_draw_probability": normalized[1],
        "delta_points": round(normalized[1] - raw[1], 2),
        "expected_goal_difference": round(abs(home_lambda - away_lambda), 3),
        "expected_total_goals": round(total_goals, 3),
        "goal_balance_score": round(
            max(0.0, 1.0 - abs(home_lambda - away_lambda) / total_goals) * 100.0,
            1,
        ),
    }
    return result, metadata
