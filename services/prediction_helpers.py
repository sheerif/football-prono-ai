import pandas as pd
from sqlalchemy import text

from database.database import engine
from services import import_service, prediction_service, stats_service
from services.season_format import season_list


def fetch_leagues():
    try:
        return pd.read_sql("SELECT id, name, country FROM leagues ORDER BY country, name", engine)
    except Exception:
        return pd.DataFrame(columns=["id", "name", "country"])


def default_league_index(options, preferred_league_id: int = 61) -> int:
    league_ids = [int(option) for option in options]
    try:
        return league_ids.index(int(preferred_league_id))
    except ValueError:
        return 0


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
    return season_list(seasons, reverse=False)


def missing_seasons_message(missing_seasons, used_seasons=None):
    seasons = _format_season_list(missing_seasons)
    message = (
        f"Saison sportive non présente dans la base: {seasons}. Elle est ignorée pour ce calcul. "
    )
    if used_seasons:
        used = _format_season_list(used_seasons)
        message += f"Saisons sportives utilisées: {used}. "
    message += "Lancez un import manuel si vous voulez ajouter cette saison sportive."
    return message


def teams_available_message(team_count: int, seasons) -> str:
    seasons_label = season_list(seasons)
    return (
        f"{team_count} équipe(s) disponible(s) pour les saisons sportives utilisées: {seasons_label}. "
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


def upcoming_fixtures(
    matches_df: pd.DataFrame,
    team_ids=None,
    now=None,
    days_ahead: int | None = None,
) -> pd.DataFrame:
    """Retourne uniquement les rencontres réelles à venir de la sélection."""
    if matches_df.empty or "date" not in matches_df:
        return pd.DataFrame(columns=matches_df.columns)

    fixtures = matches_df.copy()
    dates = pd.to_datetime(fixtures["date"], errors="coerce", utc=True)
    reference = pd.Timestamp.now(tz="UTC") if now is None else pd.to_datetime(now, utc=True)
    mask = (
        dates.ge(reference)
        & fixtures["home_goals"].isna()
        & fixtures["away_goals"].isna()
    )
    if days_ahead is not None:
        horizon = reference + pd.Timedelta(days=max(1, int(days_ahead)))
        mask &= dates.le(horizon)
    if team_ids is not None:
        selected = {int(team_id) for team_id in team_ids}
        mask &= fixtures["home_team_id"].isin(selected)
        mask &= fixtures["away_team_id"].isin(selected)
    fixtures = fixtures.loc[mask].copy()
    fixtures["_scheduled_at"] = dates.loc[mask]
    return fixtures.sort_values(["_scheduled_at", "fixture_id"]).drop(
        columns=["_scheduled_at"]
    )


def historical_context_before(matches_df: pd.DataFrame, kickoff) -> pd.DataFrame:
    """Isole les résultats connus avant un coup d'envoi donné."""
    if matches_df.empty:
        return matches_df.copy()
    dates = pd.to_datetime(matches_df["date"], errors="coerce", utc=True)
    cutoff = pd.to_datetime(kickoff, utc=True)
    return matches_df.loc[
        dates.lt(cutoff)
        & matches_df["home_goals"].notna()
        & matches_df["away_goals"].notna()
    ].copy()


def fixture_data_completeness(
    fixture_id: int,
    player_intelligence: dict | None,
    historical_match_count: int,
) -> dict:
    """Résume les quatre familles de données utiles à une analyse de fixture."""
    cache = {"details": False, "api_prediction": False}
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT
                        EXISTS(SELECT 1 FROM fixture_api_details WHERE fixture_id = :fid) AS details,
                        EXISTS(SELECT 1 FROM fixture_api_predictions WHERE fixture_id = :fid) AS api_prediction
                    """
                ),
                {"fid": int(fixture_id)},
            ).mappings().one()
            cache = {key: bool(row[key]) for key in cache}
    except Exception:
        pass

    intelligence = player_intelligence or {}
    lineup_count = sum(
        bool(intelligence.get(side)) for side in ("home", "away")
    )
    checks = {
        "historique": int(historical_match_count) >= 30,
        "détails": cache["details"],
        "comparaison API": cache["api_prediction"],
        "compositions": lineup_count == 2,
    }
    available = sum(checks.values())
    return {
        "percentage": int(round(available / len(checks) * 100)),
        "label": " · ".join(
            f"{name} {'✓' if present else '—'}"
            for name, present in checks.items()
        ),
        "checks": checks,
    }


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


def predict_match(
    matches_df: pd.DataFrame,
    home_team: int,
    away_team: int,
    player_intelligence: dict | None = None,
):
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
    completed = matches_df.dropna(subset=["home_goals", "away_goals"])
    if len(completed) >= 30:
        observed_draw_rate = float(
            (completed["home_goals"] == completed["away_goals"]).mean()
        )
    else:
        observed_draw_rate = 0.25
    observed_draw_rate = max(0.15, min(0.35, observed_draw_rate))
    details["draw_rate_baseline"] = round(observed_draw_rate * 100, 1)
    details["weights"]["Taux de nul historique"] = (
        f"ancrage à {details['draw_rate_baseline']} %"
    )
    prediction = prediction_service.predict_simple(
        home_strength,
        away_strength,
        draw_factor=observed_draw_rate,
    )
    if player_intelligence and player_intelligence.get("complete"):
        details["prediction_before_player_adjustment"] = dict(prediction)
        home_players = player_intelligence.get("home") or {}
        away_players = player_intelligence.get("away") or {}
        tactical = player_intelligence.get("tactical") or {}
        prediction, adjustment = prediction_service.adjust_prediction_for_player_form(
            prediction,
            home_form_score=home_players.get("form_score", 50),
            away_form_score=away_players.get("form_score", 50),
            reliability=player_intelligence.get("reliability", 0),
            tactical_edge=tactical.get("edge", 0),
            tactical_reliability=tactical.get("reliability", 0),
        )
        details["player_adjustment"] = adjustment
        details["tactical_analysis"] = tactical or None
        details["weights"]["Forme des joueurs prévus"] = (
            f"ajustement plafonné à ±{adjustment['max_probability_shift']:.0f} points"
        )
        details["weights"]["Opposition des dispositifs"] = (
            f"ajustement plafonné à ±{adjustment['max_tactical_probability_shift']:.0f} points"
        )
    else:
        details["prediction_before_player_adjustment"] = None
        details["player_adjustment"] = None
        details["tactical_analysis"] = None
    return prediction, home_stats, away_stats, details
