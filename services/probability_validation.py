"""Invariants partagés pour la distribution canonique et la matrice Poisson."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

KEYS = ("home_probability", "draw_probability", "away_probability")


def validate_distribution(values: Mapping[str, object] | Sequence[object], tolerance: float = 0.02) -> list[float]:
    raw = [values.get(key) for key in KEYS] if isinstance(values, Mapping) else list(values)[:3]
    if len(raw) != 3:
        raise ValueError("Une distribution 1/N/2 doit contenir trois valeurs.")
    try:
        probabilities = [float(value) for value in raw]
    except (TypeError, ValueError) as exc:
        raise ValueError("Distribution 1/N/2 invalide.") from exc
    if not all(math.isfinite(value) and 0 <= value <= 100 for value in probabilities):
        raise ValueError("Probabilités hors bornes.")
    if abs(sum(probabilities) - 100) > tolerance:
        raise ValueError("La somme 1/N/2 doit être égale à 100 %.")
    return probabilities


def validate_score_matrix(matrix: Sequence[Mapping[str, object]], probabilities: Mapping[str, object], tolerance: float = 0.08) -> None:
    """Vérifie que la matrice et son agrégation 1/N/2 sont cohérentes."""
    total = sum(float(row.get("Probabilité") or 0) for row in matrix)
    if abs(total - 100) > tolerance:
        raise ValueError("La matrice de scores ne totalise pas 100 %.")
    derived = [
        sum(float(row["Probabilité"]) for row in matrix if row["Buts domicile"] > row["Buts extérieur"]),
        sum(float(row["Probabilité"]) for row in matrix if row["Buts domicile"] == row["Buts extérieur"]),
        sum(float(row["Probabilité"]) for row in matrix if row["Buts domicile"] < row["Buts extérieur"]),
    ]
    expected = validate_distribution(probabilities)
    if any(abs(left - right) > tolerance for left, right in zip(derived, expected, strict=True)):
        raise ValueError("La distribution 1/N/2 ne correspond pas à la matrice de scores.")
