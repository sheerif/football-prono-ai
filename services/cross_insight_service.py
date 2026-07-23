import pandas as pd
from sqlalchemy import text

from database.database import engine


def _clip(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _venue_record(matches_df: pd.DataFrame, team_id: int, venue: str) -> dict:
    if venue == "home":
        rows = matches_df[matches_df["home_team_id"] == team_id]
    else:
        rows = matches_df[matches_df["away_team_id"] == team_id]
    rows = rows.dropna(subset=["home_goals", "away_goals"])
    wins = draws = 0
    for _, row in rows.iterrows():
        goals_for = row["home_goals"] if venue == "home" else row["away_goals"]
        goals_against = row["away_goals"] if venue == "home" else row["home_goals"]
        wins += int(goals_for > goals_against)
        draws += int(goals_for == goals_against)
    played = len(rows)
    return {
        "played": played,
        "wins": wins,
        "draws": draws,
        "rate": wins / played if played else 0.0,
    }


def _h2h_signal(
    matches_df: pd.DataFrame,
    home_team: int,
    away_team: int,
    limit: int = 8,
) -> dict:
    rows = matches_df[
        (
            (matches_df["home_team_id"] == home_team)
            & (matches_df["away_team_id"] == away_team)
        )
        | (
            (matches_df["home_team_id"] == away_team)
            & (matches_df["away_team_id"] == home_team)
        )
    ].dropna(subset=["home_goals", "away_goals"])
    rows = rows.sort_values("date", ascending=False).head(limit)
    home_points = away_points = 0
    for _, row in rows.iterrows():
        if row["home_team_id"] == home_team:
            home_goals, away_goals = row["home_goals"], row["away_goals"]
        else:
            home_goals, away_goals = row["away_goals"], row["home_goals"]
        if home_goals > away_goals:
            home_points += 3
        elif away_goals > home_goals:
            away_points += 3
        else:
            home_points += 1
            away_points += 1
    maximum = max(1, len(rows) * 3)
    return {
        "count": len(rows),
        "home_points": home_points,
        "away_points": away_points,
        "signal": (home_points - away_points) / maximum,
    }


def load_upcoming_api_signal(home_team: int, away_team: int) -> dict | None:
    try:
        rows = pd.read_sql(
            text(
                """
                SELECT
                    m.fixture_id,
                    m.date,
                    p.advice,
                    p.winner,
                    p.home_probability,
                    p.draw_probability,
                    p.away_probability,
                    p.updated_at
                FROM matches m
                JOIN fixture_api_predictions p
                  ON p.fixture_id = m.fixture_id
                WHERE m.home_team_id = :home_team
                  AND m.away_team_id = :away_team
                  AND m.date >= CURRENT_TIMESTAMP
                ORDER BY m.date ASC
                LIMIT 1
                """
            ),
            engine,
            params={"home_team": home_team, "away_team": away_team},
        )
    except Exception:
        return None
    if rows.empty:
        return None
    row = rows.iloc[0]
    return {
        "fixture_id": int(row["fixture_id"]),
        "date": row["date"],
        "advice": row.get("advice"),
        "winner": row.get("winner"),
        "home_probability": float(row.get("home_probability") or 0),
        "draw_probability": float(row.get("draw_probability") or 0),
        "away_probability": float(row.get("away_probability") or 0),
        "updated_at": row.get("updated_at"),
    }


def build_cross_insight(
    matches_df: pd.DataFrame,
    home_team: int,
    away_team: int,
    home_name: str,
    away_name: str,
    prediction: dict,
    score_prediction: dict,
    home_form_score: float,
    away_form_score: float,
    home_played: int,
    away_played: int,
    selected_seasons=None,
    api_signal: dict | None = None,
) -> dict:
    home_venue = _venue_record(matches_df, home_team, "home")
    away_venue = _venue_record(matches_df, away_team, "away")
    h2h = _h2h_signal(matches_df, home_team, away_team)
    api_signal = (
        api_signal
        if api_signal is not None
        else load_upcoming_api_signal(home_team, away_team)
    )

    expected_home = float(score_prediction.get("expected_home_goals") or 0)
    expected_away = float(score_prediction.get("expected_away_goals") or 0)
    factor_signals = {
        "Modèle statistique": _clip(
            (
                float(prediction.get("home_probability") or 0)
                - float(prediction.get("away_probability") or 0)
            )
            / 100
        ),
        "Forme récente": _clip(home_form_score - away_form_score),
        "Domicile / extérieur": _clip(
            home_venue["rate"] - away_venue["rate"]
        ),
        "Face-à-face": _clip(h2h["signal"]),
        "Buts attendus": _clip((expected_home - expected_away) / 3),
    }
    weights = {
        "Modèle statistique": 0.35,
        "Forme récente": 0.20,
        "Domicile / extérieur": 0.15,
        "Face-à-face": 0.15,
        "Buts attendus": 0.15,
    }
    internal_edge = sum(
        factor_signals[label] * weight for label, weight in weights.items()
    )
    api_edge = None
    if api_signal:
        api_edge = _clip(
            (
                api_signal["home_probability"]
                - api_signal["away_probability"]
            )
            / 100
        )
        edge = 0.8 * internal_edge + 0.2 * api_edge
    else:
        edge = internal_edge

    if edge >= 0.12:
        verdict = f"Avantage {home_name}"
    elif edge <= -0.12:
        verdict = f"Avantage {away_name}"
    else:
        verdict = "Match équilibré"

    sample_score = min(1.0, min(home_played, away_played) / 20)
    h2h_score = min(1.0, h2h["count"] / 5)
    venue_score = min(
        1.0, min(home_venue["played"], away_venue["played"]) / 10
    )
    reliability = round(
        100
        * (
            0.50 * sample_score
            + 0.20 * h2h_score
            + 0.20 * venue_score
            + 0.10 * int(api_signal is not None)
        )
    )
    if reliability >= 75:
        reliability_label = "bonne"
    elif reliability >= 50:
        reliability_label = "moyenne"
    else:
        reliability_label = "prudente"

    factors = []
    for label, signal in factor_signals.items():
        if signal > 0.08:
            advantage = home_name
        elif signal < -0.08:
            advantage = away_name
        else:
            advantage = "Équilibre"
        factors.append(
            {
                "factor": label,
                "advantage": advantage,
                "strength": round(abs(signal) * 100),
            }
        )
    if api_signal:
        factors.append(
            {
                "factor": "Conseil API du match à venir",
                "advantage": (
                    home_name
                    if api_edge > 0.08
                    else away_name
                    if api_edge < -0.08
                    else "Équilibre"
                ),
                "strength": round(abs(api_edge) * 100),
            }
        )

    aligned = [
        factor["factor"]
        for factor in factors
        if factor["advantage"] in {home_name, away_name}
        and (
            (edge > 0 and factor["advantage"] == home_name)
            or (edge < 0 and factor["advantage"] == away_name)
        )
    ]
    opposing = [
        factor["factor"]
        for factor in factors
        if factor["advantage"] in {home_name, away_name}
        and (
            (edge > 0 and factor["advantage"] == away_name)
            or (edge < 0 and factor["advantage"] == home_name)
        )
    ]

    caveats = []
    if min(home_played, away_played) < 10:
        caveats.append("Échantillon statistique limité pour au moins une équipe.")
    if h2h["count"] < 3:
        caveats.append("Peu de confrontations directes disponibles.")
    if not api_signal:
        caveats.append("Aucun conseil API synchronisé pour une affiche à venir.")
    if selected_seasons and len(selected_seasons) > 5:
        caveats.append(
            "La période couvre plusieurs cycles sportifs ; les données anciennes "
            "peuvent lisser la forme actuelle."
        )
    if opposing:
        caveats.append(
            "Certains signaux se contredisent : " + ", ".join(opposing) + "."
        )

    return {
        "verdict": verdict,
        "edge": round(edge * 100),
        "reliability": reliability,
        "reliability_label": reliability_label,
        "factors": factors,
        "aligned_factors": aligned,
        "opposing_factors": opposing,
        "caveats": caveats,
        "home_venue": home_venue,
        "away_venue": away_venue,
        "h2h": h2h,
        "api_signal": api_signal,
    }
