import pandas as pd
from sqlalchemy import text

from database.database import engine
from services import import_service, prediction_service, stats_service


def fetch_leagues():
    try:
        return pd.read_sql("SELECT id, name, country FROM leagues ORDER BY country, name", engine)
    except Exception:
        return pd.DataFrame(columns=["id", "name", "country"])


def fetch_seasons(league_id: int):
    try:
        df = pd.read_sql(
            text(
                """
                SELECT season FROM league_seasons WHERE league_id = :lid
                UNION
                SELECT DISTINCT season FROM matches WHERE league_id = :lid
                ORDER BY season DESC
                """
            ),
            engine,
            params={"lid": league_id},
        )
        return [int(season) for season in df["season"].tolist()]
    except Exception:
        return []


def configured_seasons():
    config = import_service.get_auto_refresh_config()
    configured = set(range(config["start_season"], config["end_season"] + 1))
    try:
        df = pd.read_sql(
            """
            SELECT season FROM league_seasons
            UNION
            SELECT DISTINCT season FROM matches
            ORDER BY season
            """,
            engine,
        )
        configured.update(int(season) for season in df["season"].dropna().tolist())
    except Exception:
        pass
    return sorted(configured)


def selected_season_status(selected_seasons, available_seasons):
    selected = [int(season) for season in selected_seasons]
    available = {int(season) for season in available_seasons}
    used = [season for season in selected if season in available]
    missing = [season for season in selected if season not in available]
    return used, missing


def _format_season_list(seasons) -> str:
    values = sorted({int(season) for season in seasons})
    if not values:
        return "aucune"
    ranges = []
    start = previous = values[0]
    for season in values[1:]:
        if season == previous + 1:
            previous = season
            continue
        ranges.append(f"{start}" if start == previous else f"{start} à {previous}")
        start = previous = season
    ranges.append(f"{start}" if start == previous else f"{start} à {previous}")
    return ", ".join(ranges)


def missing_seasons_message(missing_seasons, used_seasons=None):
    seasons = _format_season_list(missing_seasons)
    message = (
        f"Saison non présente dans la base: {seasons}. Elle est ignorée pour ce calcul. "
    )
    if used_seasons:
        used = _format_season_list(used_seasons)
        message += f"Saisons utilisées: {used}. "
    message += "Lancez un import manuel si vous voulez ajouter cette saison."
    return message


def teams_available_message(team_count: int, seasons) -> str:
    seasons_label = ", ".join(str(season) for season in seasons) if seasons else "aucune saison"
    return (
        f"{team_count} équipe(s) disponible(s) pour les saisons utilisées: {seasons_label}. "
        "La liste contient les équipes qui ont au moins un match enregistré dans la base pour cette sélection."
    )


def load_matches(league_id: int, seasons):
    if not seasons:
        return pd.DataFrame()
    placeholders = ",".join([f":s{i}" for i in range(len(seasons))])
    params = {"lid": league_id}
    params.update({f"s{i}": season for i, season in enumerate(seasons)})
    try:
        return pd.read_sql(
            text(f"SELECT * FROM matches WHERE league_id = :lid AND season IN ({placeholders}) ORDER BY date DESC"),
            engine,
            params=params,
        )
    except Exception:
        return pd.DataFrame()


def fetch_teams(matches_df: pd.DataFrame):
    if matches_df.empty:
        return {}
    team_ids = pd.unique(matches_df[["home_team_id", "away_team_id"]].values.ravel("K"))
    team_ids = [int(team_id) for team_id in team_ids if pd.notna(team_id)]
    if not team_ids:
        return {}
    try:
        teams = pd.read_sql(
            text(f"SELECT id, name FROM teams WHERE id IN ({','.join(str(team_id) for team_id in team_ids)}) ORDER BY name"),
            engine,
        )
        return {int(row.id): row.name for row in teams.itertuples()}
    except Exception:
        return {team_id: str(team_id) for team_id in team_ids}


def recent_form(matches_df: pd.DataFrame, team_id: int, limit: int = 8):
    rows = matches_df[(matches_df["home_team_id"] == team_id) | (matches_df["away_team_id"] == team_id)].copy()
    rows = rows.dropna(subset=["home_goals", "away_goals"]).head(limit)
    results = []
    for _, row in rows.iterrows():
        if row["home_team_id"] == team_id:
            gf, ga = row["home_goals"], row["away_goals"]
        else:
            gf, ga = row["away_goals"], row["home_goals"]
        results.append("W" if gf > ga else "D" if gf == ga else "L")
    return results


def format_form(results):
    labels = {"W": "V", "D": "N", "L": "D"}
    return " ".join(labels.get(result, result) for result in results) if results else "Aucune donnée"


def form_score(results):
    if not results:
        return 0.5
    points = sum(3 if result == "W" else 1 if result == "D" else 0 for result in results)
    return points / (3 * len(results))


def predict_match(matches_df: pd.DataFrame, home_team: int, away_team: int):
    home_stats = stats_service.compute_basic_stats(matches_df, home_team)
    away_stats = stats_service.compute_basic_stats(matches_df, away_team)
    home_form_results = recent_form(matches_df, home_team)
    away_form_results = recent_form(matches_df, away_team)
    home_form = form_score(home_form_results)
    away_form = form_score(away_form_results)

    home_played = max(1, home_stats["played"])
    away_played = max(1, away_stats["played"])
    home_attack = home_stats["goals_for"] / home_played
    away_attack = away_stats["goals_for"] / away_played
    home_defense = home_stats["goals_against"] / home_played
    away_defense = away_stats["goals_against"] / away_played

    home_strength = 0.55 * home_form + 0.25 * max(0.05, home_attack - away_defense + 1) + 0.20 * 0.65
    away_strength = 0.55 * away_form + 0.25 * max(0.05, away_attack - home_defense + 1) + 0.20 * 0.45
    details = {
        "home_form_results": home_form_results,
        "away_form_results": away_form_results,
        "home_form_score": round(home_form * 100, 1),
        "away_form_score": round(away_form * 100, 1),
        "home_attack": round(home_attack, 2),
        "away_attack": round(away_attack, 2),
        "home_defense": round(home_defense, 2),
        "away_defense": round(away_defense, 2),
        "home_strength": round(home_strength, 3),
        "away_strength": round(away_strength, 3),
        "weights": {
            "Forme récente": "55 %",
            "Attaque contre défense adverse": "25 %",
            "Contexte domicile/extérieur": "20 %",
        },
    }
    return prediction_service.predict_simple(home_strength, away_strength), home_stats, away_stats, details
