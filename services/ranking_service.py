"""Indice de solidité des pronostics 1/N/2.

Le score produit ici mesure la qualité globale d'un pronostic. Il ne représente
jamais une probabilité de victoire et reste indépendant de la normalisation
des probabilités 1/N/2.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence


ProbabilityCalibrator = Callable[[float], float]
OUTCOME_KEYS = (
    "home_probability",
    "draw_probability",
    "away_probability",
)


def _ratio(value: object, default: float = 0.0) -> float:
    """Convertit une valeur en ratio fini borné entre zéro et un."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if not math.isfinite(number):
        number = default
    return max(0.0, min(1.0, number))


def _probabilities(values: Mapping[str, object] | Sequence[object]) -> list[float]:
    """Retourne trois pourcentages sûrs sans modifier les valeurs métier."""
    if isinstance(values, Mapping):
        raw = [values.get(key, 0.0) for key in OUTCOME_KEYS]
    else:
        raw = list(values)[:3]
    raw += [0.0] * (3 - len(raw))
    probabilities = []
    for value in raw:
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = 0.0
        probabilities.append(
            max(0.0, min(100.0, number if math.isfinite(number) else 0.0))
        )
    return probabilities


def compute_margin(
    probabilities: Mapping[str, object] | Sequence[object],
) -> tuple[float, float]:
    """Calcule la marge en points et son score normalisé sur un seuil de 25."""
    ordered = sorted(_probabilities(probabilities), reverse=True)
    margin = max(0.0, ordered[0] - ordered[1])
    return round(margin, 2), round(min(1.0, margin / 25.0), 4)


def compute_data_quality(
    *,
    historical_match_count: int = 0,
    statistics_coverage: float | bool = 0.0,
    lineup_coverage: float | bool = 0.0,
    freshness: float = 0.0,
    api_coverage: float | bool = 0.0,
) -> float:
    """Évalue la couverture et la fraîcheur des données entre zéro et un.

    Les poids internes rendent l'historique prioritaire, puis valorisent les
    statistiques et les compositions. La fraîcheur et l'API complètent le
    signal sans rendre une source externe obligatoire.
    """
    try:
        history = max(0, int(historical_match_count))
    except (TypeError, ValueError):
        history = 0
    history_score = min(1.0, history / 30.0)
    quality = (
        0.30 * history_score
        + 0.20 * _ratio(statistics_coverage)
        + 0.20 * _ratio(lineup_coverage)
        + 0.15 * _ratio(freshness)
        + 0.15 * _ratio(api_coverage)
    )
    return round(_ratio(quality), 4)


def compute_stability(
    window_predictions: Iterable[Mapping[str, object] | Sequence[object]],
) -> float:
    """Mesure la cohérence du favori entre plusieurs fenêtres historiques.

    Un favori identique sur toutes les fenêtres vaut exactement 1. En cas de
    divergence, le score baisse selon la fréquence du favori majoritaire et
    selon la dispersion des probabilités.
    """
    windows = [_probabilities(prediction) for prediction in window_predictions]
    if not windows:
        return 0.5
    if len(windows) == 1:
        return 0.75
    favorites = [max(range(3), key=values.__getitem__) for values in windows]
    if len(set(favorites)) == 1:
        return 1.0
    majority_share = max(Counter(favorites).values()) / len(favorites)
    dispersion = sum(
        max(values[index] for values in windows)
        - min(values[index] for values in windows)
        for index in range(3)
    ) / (3 * 100)
    stability = 0.75 * majority_share + 0.25 * (1.0 - min(1.0, dispersion))
    return round(_ratio(stability), 4)


def compute_agreement(
    api_refinement: Mapping[str, object] | None,
) -> float:
    """Traduit l'accord API selon les valeurs neutres définies par le métier."""
    if not api_refinement or not api_refinement.get("applied"):
        return 0.75
    return 1.0 if api_refinement.get("agreement") is True else 0.60


def compute_ranking_score(
    probabilities: Mapping[str, object] | Sequence[object],
    *,
    data_quality: float,
    stability_score: float,
    agreement_score: float,
    calibrator: ProbabilityCalibrator | None = None,
) -> dict[str, float]:
    """Calcule l'indice de solidité et retourne toutes ses composantes.

    ``calibrator`` constitue le point d'extension pour une future calibration
    isotone ou de Platt. Il reçoit et retourne une probabilité entre zéro et un.
    """
    values = _probabilities(probabilities)
    raw_probability = max(values) / 100.0
    calibrated_probability = raw_probability
    if calibrator is not None:
        try:
            calibrated_probability = float(calibrator(raw_probability))
        except (TypeError, ValueError):
            calibrated_probability = raw_probability
    calibrated_probability = _ratio(
        calibrated_probability,
        default=raw_probability,
    )
    margin, margin_score = compute_margin(values)
    data_quality = _ratio(data_quality)
    stability_score = _ratio(stability_score, default=0.5)
    agreement_score = _ratio(agreement_score, default=0.75)
    ranking_score = 100.0 * (
        0.40 * calibrated_probability
        + 0.20 * margin_score
        + 0.15 * data_quality
        + 0.15 * stability_score
        + 0.10 * agreement_score
    )
    return {
        "ranking_score": round(max(0.0, min(100.0, ranking_score)), 2),
        "calibrated_probability": round(calibrated_probability, 4),
        "margin": margin,
        "margin_score": margin_score,
        "data_quality": round(data_quality, 4),
        "stability_score": round(stability_score, 4),
        "agreement_score": round(agreement_score, 4),
    }


def attach_ranking(
    prediction: Mapping[str, object],
    *,
    data_quality: float = 0.5,
    stability_score: float = 0.5,
    agreement_score: float = 0.75,
    calibrator: ProbabilityCalibrator | None = None,
) -> dict:
    """Ajoute l'indice à une prédiction sans altérer ses probabilités 1/N/2."""
    enriched = dict(prediction)
    values = _probabilities(enriched)
    enriched["confidence"] = float(max(values))
    enriched.update(
        compute_ranking_score(
            enriched,
            data_quality=data_quality,
            stability_score=stability_score,
            agreement_score=agreement_score,
            calibrator=calibrator,
        )
    )
    return enriched
