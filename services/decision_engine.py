"""Moteur central de décision pour les prédictions de match.

Il sépare volontairement probabilités, consensus, solidité, risque et marché
recommandé. Les interfaces consomment son résultat mais ne reconstruisent pas
la décision elles-mêmes.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from services import prediction_service, ranking_service, source_service


OUTCOME_CODES = ("1", "N", "2")
OUTCOME_KEYS = ranking_service.OUTCOME_KEYS


def _distribution(source: Mapping[str, object] | Sequence[object] | None) -> list[float] | None:
    """Lit une distribution 1/N/2 complète et la normalise à 100 %."""
    if source is None:
        return None
    if isinstance(source, Mapping):
        values = [source.get(key) for key in OUTCOME_KEYS]
    else:
        values = list(source)[:3]
    if len(values) != 3 or any(value is None for value in values):
        return None
    try:
        values = [float(value) for value in values]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(value) and value >= 0 for value in values) or sum(values) <= 0:
        return None
    return prediction_service.normalize_probs(values)


def _favorite(distribution: Sequence[float]) -> str:
    return OUTCOME_CODES[max(range(3), key=distribution.__getitem__)]


def compute_double_chances(probabilities: Mapping[str, object] | Sequence[object]) -> dict[str, float]:
    """Dérive les doubles chances de l'unique distribution 1/N/2."""
    values = _distribution(probabilities)
    if values is None:
        values = [33.33, 33.33, 33.34]
    return {
        "1X": round(values[0] + values[1], 2),
        "X2": round(values[1] + values[2], 2),
        "12": round(values[0] + values[2], 2),
    }


def compute_consensus(
    statistical_prediction: Mapping[str, object],
    *,
    ai_primary: Mapping[str, object] | None = None,
    ai_secondary: Mapping[str, object] | None = None,
    api_source: Mapping[str, object] | None = None,
    data_quality: float = 0.5,
) -> dict[str, object]:
    """Mesure la convergence de sources indépendantes sans faire une moyenne."""
    sources: list[tuple[str, list[float], float]] = []
    source_states = {"statistical": {"status": source_service.AVAILABLE}}
    model = _distribution(statistical_prediction)
    if model is None:
        raise ValueError("La distribution statistique 1/N/2 est obligatoire.")
    sources.append(("modèle", model, 1.0))
    for key, label, source in (
        ("ai_a", "IA A", ai_primary),
        ("ai_b", "IA B", ai_secondary),
        ("api", "API", api_source),
    ):
        described = source_service.describe(label, source)
        source_states[key] = described
        distribution = described["distribution"]
        if described["status"] != source_service.AVAILABLE or distribution is None:
            continue
        sources.append((label, distribution, float(described["reliability"] or 0.7)))

    favorites = [_favorite(distribution) for _, distribution, _ in sources]
    base_favorite = favorites[0]
    agreements = sum(favorite == base_favorite for favorite in favorites) / len(favorites)
    distances = [
        sum(abs(model[index] - distribution[index]) for index in range(3)) / 2
        for _, distribution, _ in sources[1:]
    ]
    proximity = 1.0 if not distances else max(0.0, 1.0 - sum(distances) / len(distances) / 45.0)
    reliability = sum(weight for _, _, weight in sources) / len(sources)
    quality = max(0.0, min(1.0, float(data_quality)))
    score = 100 * (0.45 * agreements + 0.30 * proximity + 0.15 * reliability + 0.10 * quality)
    if len(sources) == 1:
        level = "interne"
    elif agreements == 1 and proximity >= 0.72:
        level = "convergence_forte"
    elif agreements >= 0.5:
        level = "convergence_partielle"
    else:
        level = "divergence"
    return {
        "score": round(max(0.0, min(100.0, score)), 2),
        "level": level,
        "sources": [label for label, _, _ in sources],
        "favorites": dict(zip([label for label, _, _ in sources], favorites, strict=True)),
        "agreement_ratio": round(agreements, 4),
        "proximity": round(proximity, 4),
        "source_states": source_states,
    }


def compute_risk(
    prediction: Mapping[str, object],
    consensus: Mapping[str, object],
    *,
    data_quality: float,
    stability_score: float,
) -> dict[str, object]:
    """Calcule un risque explicable, indépendant de la probabilité principale."""
    margin, _ = ranking_service.compute_margin(prediction)
    margin_risk = 1.0 - min(1.0, margin / 20.0)
    divergence_risk = 1.0 - float(consensus.get("agreement_ratio") or 0.0)
    quality_risk = 1.0 - max(0.0, min(1.0, float(data_quality)))
    stability_risk = 1.0 - max(0.0, min(1.0, float(stability_score)))
    score = 100 * (
        0.40 * margin_risk
        + 0.30 * divergence_risk
        + 0.15 * quality_risk
        + 0.15 * stability_risk
    )
    level = "très élevé" if score >= 80 else "élevé" if score >= 65 else "modéré" if score >= 35 else "faible"
    return {"score": round(max(0.0, min(100.0, score)), 2), "level": level}


def recommend_market(
    prediction: Mapping[str, object],
    risk: Mapping[str, object],
) -> dict[str, object]:
    """Choisit un marché dérivé des probabilités, ou la prudence."""
    values = _distribution(prediction)
    if values is None:
        return {"market": "PRUDENCE", "probability": None, "reason": "Distribution indisponible."}
    ordered = sorted(range(3), key=values.__getitem__, reverse=True)
    margin = values[ordered[0]] - values[ordered[1]]
    favorite = OUTCOME_CODES[ordered[0]]
    if values[ordered[0]] >= 55 and margin >= 12 and risk["level"] != "élevé":
        return {"market": favorite, "probability": values[ordered[0]], "reason": "Scénario principal suffisamment détaché."}
    pair = "".join(sorted((OUTCOME_CODES[ordered[0]], OUTCOME_CODES[ordered[1]]), key=("1", "N", "2").index))
    market = {"1N": "1X", "N2": "X2", "12": "12"}[pair]
    double_chances = compute_double_chances(values)
    if double_chances[market] >= 65 and risk["level"] != "élevé":
        return {"market": market, "probability": double_chances[market], "reason": "Match trop serré pour une victoire sèche."}
    return {"market": "PRUDENCE", "probability": None, "reason": "Aucun marché ne présente une sécurité suffisante."}


def calculate(
    statistical_prediction: Mapping[str, object],
    *,
    data_quality: float,
    stability_score: float,
    api_source: Mapping[str, object] | None = None,
    ai_primary: Mapping[str, object] | None = None,
    ai_secondary: Mapping[str, object] | None = None,
    probability_calibrator: ranking_service.ProbabilityCalibrator | None = None,
) -> dict[str, object]:
    """Produit une décision finale explicable à partir du socle statistique."""
    consensus = compute_consensus(
        statistical_prediction,
        ai_primary=ai_primary,
        ai_secondary=ai_secondary,
        api_source=api_source,
        data_quality=data_quality,
    )
    agreement_score = max(0.60, float(consensus["score"]) / 100)
    prediction = ranking_service.attach_ranking(
        statistical_prediction,
        data_quality=data_quality,
        stability_score=stability_score,
        agreement_score=agreement_score,
        calibrator=probability_calibrator,
    )
    risk = compute_risk(
        prediction,
        consensus,
        data_quality=data_quality,
        stability_score=stability_score,
    )
    recommendation = recommend_market(prediction, risk)
    available_sources = [
        name for name, source in consensus["source_states"].items()
        if source.get("status") == source_service.AVAILABLE
    ]
    explanation = (
        f"Sources disponibles : {', '.join(available_sources)}. "
        f"Consensus {consensus['level'].replace('_', ' ')}, marge de {prediction['margin']} points, "
        f"risque {risk['level']}. {recommendation['reason']}"
    )
    prediction.update(
        {
            "double_chances": compute_double_chances(prediction),
            "consensus_score": consensus["score"],
            "consensus_level": consensus["level"],
            "risk_score": risk["score"],
            "risk_level": risk["level"],
            "recommended_market": recommendation["market"],
            "scenario_probability": prediction["confidence"],
            "sources": consensus["source_states"],
            "explanation": explanation,
        }
    )
    return {
        "prediction": prediction,
        "consensus": consensus,
        "risk": risk,
        "recommendation": recommendation,
    }
