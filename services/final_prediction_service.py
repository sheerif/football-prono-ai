"""Point d'entrée partagé pour le calcul final d'une prédiction de match."""

from __future__ import annotations

import inspect

import pandas as pd

from services import lineup_service, prediction_helpers, prediction_service


def _internal_only_refinement(prediction: dict, reason: str) -> tuple[dict, dict]:
    probabilities = [
        float(prediction.get("home_probability") or 0),
        float(prediction.get("draw_probability") or 0),
        float(prediction.get("away_probability") or 0),
    ]
    return (
        {**prediction, "api_blend_applied": False},
        {
            "applied": False,
            "api_weight": 0.0,
            "internal_probabilities": probabilities,
            "api_probabilities": None,
            "agreement": None,
            "maximum_gap": None,
            "api_quality": 0.0,
            "favored_outcomes": [],
            "ignored_reason": reason,
        },
    )


def _fallback_consensus_advice(
    prediction: dict,
    home_name: str,
    away_name: str,
) -> dict:
    labels = {"1": home_name, "N": "Match nul", "2": away_name}
    probabilities = {
        "1": float(prediction.get("home_probability") or 0),
        "N": float(prediction.get("draw_probability") or 0),
        "2": float(prediction.get("away_probability") or 0),
    }
    ordered = sorted(probabilities, key=probabilities.get, reverse=True)
    main, alternative = ordered[:2]
    top_two = round(probabilities[main] + probabilities[alternative], 2)
    margin = round(probabilities[main] - probabilities[alternative], 2)
    return {
        "main_code": main,
        "main_label": labels[main],
        "main_probability": round(probabilities[main], 2),
        "alternative_code": alternative,
        "alternative_label": labels[alternative],
        "alternative_probability": round(probabilities[alternative], 2),
        "top_two_coverage": top_two,
        "margin": margin,
        "level": "interne",
        "message": (
            f"La lecture interne place {labels[main]} devant "
            f"{labels[alternative]} ({top_two} % pour les deux scénarios)."
        ),
    }


def _supported_kwargs(function, kwargs: dict) -> dict:
    try:
        parameters = inspect.signature(function).parameters
    except (TypeError, ValueError):
        return kwargs
    if any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    ):
        return kwargs
    return {key: value for key, value in kwargs.items() if key in parameters}


def calculate(
    matches_df: pd.DataFrame,
    home_team: int,
    away_team: int,
    home_name: str,
    away_name: str,
    *,
    player_intelligence: dict | None = None,
    api_signal: dict | None = None,
    score_top_n: int = 6,
) -> dict:
    """Calcule une fois les probabilités, le poids API et le scénario final.

    Les pages peuvent présenter le résultat différemment, mais elles ne doivent
    plus recomposer elles-mêmes ces éléments métier.
    """
    predict_match = prediction_helpers.predict_match
    internal, home_stats, away_stats, details = predict_match(
        matches_df,
        int(home_team),
        int(away_team),
        **_supported_kwargs(
            predict_match,
            {"player_intelligence": player_intelligence},
        ),
    )
    internal_prediction = dict(internal)
    blend_prediction = getattr(
        prediction_service,
        "blend_with_api_prediction",
        None,
    )
    if callable(blend_prediction):
        prediction, api_refinement = blend_prediction(internal, api_signal)
    else:
        prediction, api_refinement = _internal_only_refinement(
            internal,
            "Fusion API indisponible sur cette instance ; modèle interne conservé.",
        )
    build_advice = getattr(prediction_service, "build_consensus_advice", None)
    if callable(build_advice):
        consensus_advice = build_advice(
            prediction,
            api_refinement,
            str(home_name),
            str(away_name),
        )
    else:
        consensus_advice = _fallback_consensus_advice(
            prediction,
            str(home_name),
            str(away_name),
        )
    details["api_refinement"] = api_refinement
    details["consensus_advice"] = consensus_advice
    player_goal_factors = getattr(lineup_service, "player_goal_factors", None)
    if callable(player_goal_factors):
        home_player_factor, away_player_factor = player_goal_factors(
            player_intelligence or {}
        )
    else:
        home_player_factor, away_player_factor = 1.0, 1.0
    predict_scorelines = prediction_service.predict_scorelines
    score_prediction = predict_scorelines(
        matches_df,
        int(home_team),
        int(away_team),
        **_supported_kwargs(
            predict_scorelines,
            {
                "home_form_score": details["home_form_score"] / 100,
                "away_form_score": details["away_form_score"] / 100,
                "home_player_factor": home_player_factor,
                "away_player_factor": away_player_factor,
                "top_n": max(1, int(score_top_n)),
            },
        ),
    )
    return {
        "prediction": prediction,
        "internal_prediction": internal_prediction,
        "home_stats": home_stats,
        "away_stats": away_stats,
        "model_details": details,
        "api_refinement": api_refinement,
        "consensus_advice": consensus_advice,
        "score_prediction": score_prediction,
    }
