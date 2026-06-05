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
    arr = np.array(arr, dtype=float)
    s = arr.sum()
    if s==0:
        return [33.33,33.33,33.33]
    return list((arr/s*100).round(2))


def predict_simple(home_strength, away_strength, draw_factor=0.2):
    """home_strength/away_strength scalars combine features into probabilities"""
    home = home_strength
    away = away_strength
    draw = draw_factor*min(home, away)
    probs = normalize_probs([home, draw, away])
    confidence = max(probs)
    return {
        'home_probability': probs[0],
        'draw_probability': probs[1],
        'away_probability': probs[2],
        'confidence': float(confidence)
    }


def _safe_average(value, fallback):
    try:
        if pd.isna(value):
            return fallback
        value = float(value)
    except Exception:
        return fallback
    return value if value > 0 else fallback


def _poisson_probability(expected_goals: float, goals: int) -> float:
    return math.exp(-expected_goals) * expected_goals**goals / math.factorial(goals)


def predict_scorelines(
    matches_df,
    home_team_id: int,
    away_team_id: int,
    home_form_score: float = 0.5,
    away_form_score: float = 0.5,
    max_goals: int = 6,
    top_n: int = 6,
):
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

    expected_home = max(0.15, min(4.5, expected_home))
    expected_away = max(0.15, min(4.5, expected_away))

    score_rows = []
    for home_goals in range(max_goals + 1):
        home_prob = _poisson_probability(expected_home, home_goals)
        for away_goals in range(max_goals + 1):
            probability = home_prob * _poisson_probability(expected_away, away_goals)
            if home_goals == max_goals or away_goals == max_goals:
                probability *= 0.88
            score_rows.append(
                {
                    "Score": f"{home_goals}-{away_goals}",
                    "Probabilité": round(probability * 100, 2),
                    "Buts domicile": home_goals,
                    "Buts extérieur": away_goals,
                }
            )

    score_rows = sorted(score_rows, key=lambda row: row["Probabilité"], reverse=True)[:top_n]
    return {
        "expected_home_goals": round(expected_home, 2),
        "expected_away_goals": round(expected_away, 2),
        "scores": score_rows,
        "method": "Modèle de Poisson alimenté par attaque, défense, moyenne de ligue, forme récente et confrontations directes disponibles.",
    }
