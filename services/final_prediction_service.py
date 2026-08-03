"""Point d'entrée partagé pour le calcul final d'une prédiction de match."""

from __future__ import annotations

import inspect

import pandas as pd

from services import (
    lineup_service,
    prediction_helpers,
    prediction_service,
    ranking_service,
)


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


def _recent_team_context(
    matches_df: pd.DataFrame,
    home_team: int,
    away_team: int,
    limit: int,
) -> pd.DataFrame:
    """Construit une fenêtre contenant les derniers matchs de chaque équipe."""
    required = {"home_team_id", "away_team_id", "home_goals", "away_goals"}
    if matches_df.empty or not required.issubset(matches_df.columns):
        return pd.DataFrame(columns=matches_df.columns)
    completed = matches_df.dropna(subset=["home_goals", "away_goals"]).copy()
    if "date" in completed:
        completed["_ranking_date"] = pd.to_datetime(
            completed["date"], errors="coerce", utc=True
        )
        completed = completed.sort_values("_ranking_date", ascending=False)
    selected_indexes: set = set()
    for team_id in (int(home_team), int(away_team)):
        team_matches = completed[
            (completed["home_team_id"] == team_id)
            | (completed["away_team_id"] == team_id)
        ].head(max(1, int(limit)))
        selected_indexes.update(team_matches.index.tolist())
    return completed.loc[list(selected_indexes)].drop(
        columns=["_ranking_date"], errors="ignore"
    )


def _prediction_stability(
    predict_match,
    matches_df: pd.DataFrame,
    home_team: int,
    away_team: int,
    full_prediction: dict,
) -> float:
    """Compare le favori sur 5 matchs, 10 matchs et toute la période."""
    windows = [full_prediction]
    for limit in (5, 10):
        context = _recent_team_context(
            matches_df, home_team, away_team, limit
        )
        if context.empty:
            continue
        try:
            window_prediction, *_ = predict_match(
                context,
                int(home_team),
                int(away_team),
            )
        except Exception:
            continue
        windows.append(window_prediction)
    return ranking_service.compute_stability(windows)


def _freshness_score(matches_df: pd.DataFrame, reference_date=None) -> float:
    """Évalue la fraîcheur du dernier résultat avec une décroissance à 180 jours."""
    if matches_df.empty or "date" not in matches_df:
        return 0.0
    dates = pd.to_datetime(matches_df["date"], errors="coerce", utc=True).dropna()
    if dates.empty:
        return 0.0
    reference = pd.to_datetime(reference_date, errors="coerce", utc=True)
    if pd.isna(reference):
        reference = pd.Timestamp.now(tz="UTC")
    age_days = max(0.0, (reference - dates.max()).total_seconds() / 86400)
    if age_days <= 7:
        return 1.0
    return round(max(0.0, 1.0 - (age_days - 7) / 173), 4)


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
    match_date=None,
    probability_calibrator: ranking_service.ProbabilityCalibrator | None = None,
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
    statistics_coverage = (
        float(int((home_stats or {}).get("played") or 0) > 0)
        + float(int((away_stats or {}).get("played") or 0) > 0)
    ) / 2
    intelligence = player_intelligence or {}
    lineup_coverage = (
        float(bool(intelligence.get("home")))
        + float(bool(intelligence.get("away")))
    ) / 2
    api_coverage = 0.0
    if api_signal:
        api_coverage = max(
            0.5,
            float(api_refinement.get("api_quality") or 0.0),
        )
    data_quality = ranking_service.compute_data_quality(
        historical_match_count=len(matches_df),
        statistics_coverage=statistics_coverage,
        lineup_coverage=lineup_coverage,
        freshness=_freshness_score(matches_df, match_date),
        api_coverage=api_coverage,
    )
    stability_score = _prediction_stability(
        predict_match,
        matches_df,
        int(home_team),
        int(away_team),
        internal_prediction,
    )
    agreement_score = ranking_service.compute_agreement(api_refinement)
    prediction = ranking_service.attach_ranking(
        prediction,
        data_quality=data_quality,
        stability_score=stability_score,
        agreement_score=agreement_score,
        calibrator=probability_calibrator,
    )
    details["api_refinement"] = api_refinement
    details["consensus_advice"] = consensus_advice
    details["ranking"] = {
        key: prediction[key]
        for key in (
            "ranking_score",
            "calibrated_probability",
            "margin",
            "margin_score",
            "data_quality",
            "stability_score",
            "agreement_score",
        )
    }
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
