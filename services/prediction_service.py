import math

import numpy as np
import pandas as pd

# Placeholder simple engine combining features with weights described in spec
WEIGHTS = {
    'recent_form': 0.40,
    'ranking': 0.20,
    'home_away': 0.15,
    'h2h': 0.15,
    'off_def': 0.10,
}

def normalize_probs(arr):
    """Normalize 1/N/2 values to finite, non-negative percentages."""
    values = np.asarray(list(arr), dtype=float).reshape(-1)
    if values.size != 3:
        values = np.resize(values, 3)
    values = np.nan_to_num(values, nan=0.0, posinf=0.0, neginf=0.0)
    values = np.clip(values, 0.0, None)
    total = float(values.sum())
    if not math.isfinite(total) or total <= 0:
        values = np.ones(3, dtype=float)
        total = 3.0
    rounded = np.round(values / total * 100.0, 2)
    rounded[-1] = round(100.0 - float(rounded[:-1].sum()), 2)
    return [float(value) for value in rounded]


def predict_simple(home_strength, away_strength, draw_factor=0.25):
    """Convertit deux indices de force en probabilités 1/N/2.

    ``draw_factor`` représente le taux de nul de référence (idéalement observé
    dans le championnat), et non un multiplicateur de la plus petite force.
    La proximité des équipes fait légèrement varier ce taux. Cette formulation
    évite de rendre le nul structurellement impossible comme issue principale.
    """
    def _strength(value):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return 0.5
        return value if math.isfinite(value) and value >= 0 else 0.5
    home = _strength(home_strength)
    away = _strength(away_strength)
    try:
        draw_factor = float(draw_factor)
    except (TypeError, ValueError):
        draw_factor = 0.25
    draw_factor = draw_factor if math.isfinite(draw_factor) else 0.25
    draw_factor = max(0.12, min(0.38, draw_factor))

    total_strength = home + away
    if total_strength <= 0:
        home_share = 0.5
        balance = 1.0
    else:
        home_share = home / total_strength
        balance = 1.0 - abs(home - away) / total_strength

    # Une affiche équilibrée augmente le nul de 2 points au maximum ; un très
    # gros écart le diminue de 2 points. Le taux reste ancré dans l'historique.
    draw_probability = max(
        0.10,
        min(0.40, draw_factor + 0.04 * (balance - 0.5)),
    )
    decisive_probability = 1.0 - draw_probability
    probs = normalize_probs([
        decisive_probability * home_share,
        draw_probability,
        decisive_probability * (1.0 - home_share),
    ])
    confidence = max(probs)
    return {
        'home_probability': probs[0],
        'draw_probability': probs[1],
        'away_probability': probs[2],
        'confidence': float(confidence)
    }


def blend_with_api_prediction(
    prediction: dict,
    api_signal: dict | None,
    api_weight: float = 0.20,
) -> tuple[dict, dict]:
    """Combine prudemment le modèle interne et les probabilités API.

    La source externe reste minoritaire et la fonction conserve les deux jeux
    de probabilités afin que l'interface puisse expliquer l'accord ou le
    désaccord entre les sources.
    """
    internal = normalize_probs([
        prediction.get("home_probability", 33.33),
        prediction.get("draw_probability", 33.33),
        prediction.get("away_probability", 33.34),
    ])
    details = {
        "applied": False,
        "api_weight": 0.0,
        "internal_probabilities": internal,
        "api_probabilities": None,
        "agreement": None,
        "maximum_gap": None,
    }
    if not api_signal:
        return {**prediction, "api_blend_applied": False}, details

    raw_api = [
        api_signal.get("home_probability"),
        api_signal.get("draw_probability"),
        api_signal.get("away_probability"),
    ]
    try:
        numeric_api = [float(value) for value in raw_api]
    except (TypeError, ValueError):
        return {**prediction, "api_blend_applied": False}, details
    if not all(math.isfinite(value) and value >= 0 for value in numeric_api):
        return {**prediction, "api_blend_applied": False}, details
    if sum(numeric_api) <= 0:
        return {**prediction, "api_blend_applied": False}, details

    api = normalize_probs(numeric_api)
    try:
        weight = float(api_weight)
    except (TypeError, ValueError):
        weight = 0.20
    weight = max(0.0, min(0.30, weight if math.isfinite(weight) else 0.20))
    blended = normalize_probs([
        (1.0 - weight) * internal[index] + weight * api[index]
        for index in range(3)
    ])
    labels = ("1", "N", "2")
    internal_pick = labels[max(range(3), key=internal.__getitem__)]
    api_pick = labels[max(range(3), key=api.__getitem__)]
    details = {
        "applied": True,
        "api_weight": round(weight, 2),
        "internal_probabilities": internal,
        "api_probabilities": api,
        "agreement": internal_pick == api_pick,
        "internal_pick": internal_pick,
        "api_pick": api_pick,
        "maximum_gap": round(max(abs(internal[i] - api[i]) for i in range(3)), 2),
        "fixture_id": api_signal.get("fixture_id"),
        "advice": api_signal.get("advice"),
        "winner": api_signal.get("winner"),
        "updated_at": api_signal.get("updated_at"),
    }
    refined = {
        **prediction,
        "home_probability": blended[0],
        "draw_probability": blended[1],
        "away_probability": blended[2],
        "confidence": float(max(blended)),
        "api_blend_applied": True,
        "api_weight": round(weight, 2),
        "internal_prediction": {
            "home_probability": internal[0],
            "draw_probability": internal[1],
            "away_probability": internal[2],
            "confidence": float(max(internal)),
        },
    }
    return refined, details


def adjust_prediction_for_player_form(
    prediction: dict,
    home_form_score: float,
    away_form_score: float,
    reliability: float,
    tactical_edge: float = 0.0,
    tactical_reliability: float = 0.0,
    max_adjustment: float = 6.0,
    max_tactical_adjustment: float = 3.0,
    max_combined_adjustment: float = 8.0,
) -> tuple[dict, dict]:
    """Ajuste 1/N/2 avec des impacts joueurs et tactiques plafonnés."""
    def _finite(value, fallback):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return fallback
        return value if math.isfinite(value) else fallback

    reliability = max(0.0, min(1.0, _finite(reliability, 0.0)))
    home_form_score = max(0.0, min(100.0, _finite(home_form_score, 50.0)))
    away_form_score = max(0.0, min(100.0, _finite(away_form_score, 50.0)))
    form_edge = home_form_score - away_form_score
    requested_delta = max(-max_adjustment, min(max_adjustment, form_edge * 0.10))
    player_delta = requested_delta * reliability
    tactical_reliability = max(
        0.0, min(1.0, _finite(tactical_reliability, 0.0))
    )
    requested_tactical_delta = max(
        -max_tactical_adjustment,
        min(max_tactical_adjustment, _finite(tactical_edge, 0.0) * 0.20),
    )
    tactical_delta = requested_tactical_delta * tactical_reliability
    applied_delta = max(
        -max_combined_adjustment,
        min(max_combined_adjustment, player_delta + tactical_delta),
    )

    base = normalize_probs([
        prediction.get("home_probability", 33.33),
        prediction.get("draw_probability", 33.33),
        prediction.get("away_probability", 33.34),
    ])
    raw = [
        max(0.0, base[0] + applied_delta),
        max(0.0, base[1]),
        max(0.0, base[2] - applied_delta),
    ]
    probabilities = normalize_probs(raw)
    adjusted = {
        **prediction,
        "home_probability": probabilities[0],
        "draw_probability": probabilities[1],
        "away_probability": probabilities[2],
        "confidence": float(max(probabilities)),
    }
    details = {
        "home_player_form_score": round(home_form_score, 1),
        "away_player_form_score": round(away_form_score, 1),
        "reliability": round(reliability, 3),
        "player_probability_shift": round(player_delta, 2),
        "tactical_edge": round(_finite(tactical_edge, 0.0), 2),
        "tactical_reliability": round(tactical_reliability, 3),
        "tactical_probability_shift": round(tactical_delta, 2),
        "probability_shift": round(applied_delta, 2),
        "max_probability_shift": float(max_adjustment),
        "max_tactical_probability_shift": float(max_tactical_adjustment),
        "max_combined_probability_shift": float(max_combined_adjustment),
    }
    return adjusted, details


def _safe_average(value, fallback):
    try:
        if pd.isna(value):
            return fallback
        value = float(value)
    except Exception:
        return fallback
    return value if math.isfinite(value) and value >= 0 else fallback


def _poisson_probability(expected_goals: float, goals: int) -> float:
    expected_goals = max(0.0, float(expected_goals))
    if expected_goals == 0.0:
        return 1.0 if goals == 0 else 0.0
    return math.exp(-expected_goals) * expected_goals**goals / math.factorial(goals)


def predict_scorelines(
    matches_df,
    home_team_id: int,
    away_team_id: int,
    home_form_score: float = 0.5,
    away_form_score: float = 0.5,
    home_player_factor: float = 1.0,
    away_player_factor: float = 1.0,
    max_goals: int = 6,
    top_n: int = 6,
):
    try:
        max_goals = max(1, int(max_goals))
    except (TypeError, ValueError):
        max_goals = 6
    try:
        top_n = max(1, int(top_n))
    except (TypeError, ValueError):
        top_n = 6
    # Form factors are ratios in this model; keep bad inputs bounded.
    try:
        home_form_score = float(home_form_score)
    except (TypeError, ValueError):
        home_form_score = 0.5
    try:
        away_form_score = float(away_form_score)
    except (TypeError, ValueError):
        away_form_score = 0.5
    home_form_score = max(0.0, min(1.0, home_form_score if math.isfinite(home_form_score) else 0.5))
    away_form_score = max(0.0, min(1.0, away_form_score if math.isfinite(away_form_score) else 0.5))
    completed = matches_df.dropna(subset=["home_goals", "away_goals"]).copy()
    if completed.empty:
        return {
            "expected_home_goals": 0,
            "expected_away_goals": 0,
            "scores": [],
            "method": "Aucun match terminé disponible pour estimer les scores.",
        }

    completed["home_goals"] = completed["home_goals"].astype(float)
    completed["away_goals"] = completed["away_goals"].astype(float)

    league_home_avg = _safe_average(completed["home_goals"].mean(), 1.35)
    league_away_avg = _safe_average(completed["away_goals"].mean(), 1.10)

    home_at_home = completed[completed["home_team_id"] == home_team_id]
    away_away = completed[completed["away_team_id"] == away_team_id]
    home_all = completed[(completed["home_team_id"] == home_team_id) | (completed["away_team_id"] == home_team_id)]
    away_all = completed[(completed["home_team_id"] == away_team_id) | (completed["away_team_id"] == away_team_id)]

    def team_goals_for(df, team_id):
        values = []
        for _, row in df.iterrows():
            values.append(row["home_goals"] if row["home_team_id"] == team_id else row["away_goals"])
        return np.mean(values) if values else np.nan

    def team_goals_against(df, team_id):
        values = []
        for _, row in df.iterrows():
            values.append(row["away_goals"] if row["home_team_id"] == team_id else row["home_goals"])
        return np.mean(values) if values else np.nan

    home_attack = _safe_average(home_at_home["home_goals"].mean(), team_goals_for(home_all, home_team_id))
    home_attack = _safe_average(home_attack, league_home_avg)
    home_defense_allowed = _safe_average(home_at_home["away_goals"].mean(), team_goals_against(home_all, home_team_id))
    home_defense_allowed = _safe_average(home_defense_allowed, league_away_avg)

    away_attack = _safe_average(away_away["away_goals"].mean(), team_goals_for(away_all, away_team_id))
    away_attack = _safe_average(away_attack, league_away_avg)
    away_defense_allowed = _safe_average(away_away["home_goals"].mean(), team_goals_against(away_all, away_team_id))
    away_defense_allowed = _safe_average(away_defense_allowed, league_home_avg)

    expected_home = (
        0.38 * home_attack
        + 0.34 * away_defense_allowed
        + 0.18 * league_home_avg
        + 0.10 * max(0.65, min(1.35, 0.75 + home_form_score / 2))
    )
    expected_away = (
        0.38 * away_attack
        + 0.34 * home_defense_allowed
        + 0.18 * league_away_avg
        + 0.10 * max(0.65, min(1.35, 0.75 + away_form_score / 2))
    )

    h2h = completed[
        ((completed["home_team_id"] == home_team_id) & (completed["away_team_id"] == away_team_id))
        | ((completed["home_team_id"] == away_team_id) & (completed["away_team_id"] == home_team_id))
    ].sort_values(["date", "season"], ascending=[False, False]).head(8)
    if not h2h.empty:
        h2h_home_goals = []
        h2h_away_goals = []
        for _, row in h2h.iterrows():
            if row["home_team_id"] == home_team_id:
                h2h_home_goals.append(row["home_goals"])
                h2h_away_goals.append(row["away_goals"])
            else:
                h2h_home_goals.append(row["away_goals"])
                h2h_away_goals.append(row["home_goals"])
        expected_home = 0.82 * expected_home + 0.18 * _safe_average(np.mean(h2h_home_goals), expected_home)
        expected_away = 0.82 * expected_away + 0.18 * _safe_average(np.mean(h2h_away_goals), expected_away)

    home_player_factor = max(0.92, min(1.08, float(home_player_factor or 1)))
    away_player_factor = max(0.92, min(1.08, float(away_player_factor or 1)))
    expected_home *= home_player_factor
    expected_away *= away_player_factor

    expected_home = max(0.15, min(4.5, expected_home))
    expected_away = max(0.15, min(4.5, expected_away))

    score_rows = []
    for home_goals in range(max_goals + 1):
        home_prob = _poisson_probability(expected_home, home_goals)
        for away_goals in range(max_goals + 1):
            probability = home_prob * _poisson_probability(expected_away, away_goals)
            if home_goals == max_goals or away_goals == max_goals:
                probability *= 0.88
            score_rows.append({
                "Score": f"{home_goals}-{away_goals}",
                "raw_probability": probability,
                "Buts domicile": home_goals,
                "Buts extérieur": away_goals,
            })

    # The grid is truncated (and edge cells discounted), therefore normalize
    # before displaying percentages so the complete score grid sums to 100%.
    grid_total = sum(row["raw_probability"] for row in score_rows)
    grid_total = grid_total if math.isfinite(grid_total) and grid_total > 0 else 1.0
    for row in score_rows:
        row["Probabilité"] = round(row.pop("raw_probability") / grid_total * 100.0, 2)
    score_rows = sorted(score_rows, key=lambda row: row["Probabilité"], reverse=True)[:top_n]
    return {
        "expected_home_goals": round(expected_home, 2),
        "expected_away_goals": round(expected_away, 2),
        "scores": score_rows,
        "player_factors": {
            "home": round(home_player_factor, 3),
            "away": round(away_player_factor, 3),
        },
        "method": "Modèle de Poisson alimenté par attaque, défense, moyenne de ligue, forme récente, confrontations directes et forme des joueurs lorsqu’elle est disponible.",
    }
