"""Point d'entrée partagé pour le calcul final d'une prédiction de match."""

from __future__ import annotations

import pandas as pd

from services import lineup_service, prediction_helpers, prediction_service


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
    internal, home_stats, away_stats, details = prediction_helpers.predict_match(
        matches_df,
        int(home_team),
        int(away_team),
        player_intelligence=player_intelligence,
    )
    internal_prediction = dict(internal)
    prediction, api_refinement = prediction_service.blend_with_api_prediction(
        internal,
        api_signal,
    )
    consensus_advice = prediction_service.build_consensus_advice(
        prediction,
        api_refinement,
        str(home_name),
        str(away_name),
    )
    details["api_refinement"] = api_refinement
    details["consensus_advice"] = consensus_advice
    home_player_factor, away_player_factor = lineup_service.player_goal_factors(
        player_intelligence or {}
    )
    score_prediction = prediction_service.predict_scorelines(
        matches_df,
        int(home_team),
        int(away_team),
        home_form_score=details["home_form_score"] / 100,
        away_form_score=details["away_form_score"] / 100,
        home_player_factor=home_player_factor,
        away_player_factor=away_player_factor,
        top_n=max(1, int(score_top_n)),
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
