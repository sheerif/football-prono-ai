import itertools

import pandas as pd
import streamlit as st

from components import ui
from services import prediction_helpers
from services.season_format import season_period


def _glossary_table() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Terme": "Pronostic conseillé",
                "Définition": "Issue qui ressort le plus fortement dans le modèle: victoire domicile, match nul ou victoire extérieur.",
            },
            {
                "Terme": "Code 1N2",
                "Définition": "1 = victoire de l’équipe à domicile, N = match nul, 2 = victoire de l’équipe à l’extérieur.",
            },
            {
                "Terme": "Probabilité retenue",
                "Définition": "Pourcentage estimé pour le pronostic conseillé.",
            },
            {
                "Terme": "Confiance",
                "Définition": "Pourcentage de l’issue la plus probable parmi victoire domicile, match nul et victoire extérieur. Ce n’est pas une certitude.",
            },
        ]
    )


def _confidence_label(confidence: float) -> str:
    if confidence >= 70:
        return "signal fort"
    if confidence >= 60:
        return "signal intéressant"
    if confidence >= 50:
        return "match ouvert"
    return "signal faible"


def _argument_sentence(home_name: str, away_name: str, pick: str, confidence: float, details: dict) -> str:
    if pick == "Match nul":
        return "Match serré: forces proches."
    if pick == home_name:
        if details["home_form_score"] >= details["away_form_score"]:
            return f"Avantage {home_name}: meilleure forme."
        if details["home_attack"] >= details["away_attack"]:
            return f"Avantage {home_name}: attaque plus haute."
        return f"Avantage {home_name}: profil plus solide."
    if details["away_form_score"] >= details["home_form_score"]:
        return f"Avantage {away_name}: meilleure forme."
    if details["away_attack"] >= details["home_attack"]:
        return f"Avantage {away_name}: attaque plus haute."
    return f"Avantage {away_name}: profil plus solide."


def _build_rankings(matches_df: pd.DataFrame, team_options: dict[int, str], limit: int):
    rows = []
    team_ids = list(team_options.keys())
    for home_team, away_team in itertools.permutations(team_ids, 2):
        pred, _, _, details = prediction_helpers.predict_match(matches_df, home_team, away_team)
        home_name = team_options[home_team]
        away_name = team_options[away_team]
        outcomes = [
            ("1", "Victoire domicile", home_name, pred["home_probability"]),
            ("N", "Match nul", "Match nul", pred["draw_probability"]),
            ("2", "Victoire extérieur", away_name, pred["away_probability"]),
        ]
        code, market_label, pick, probability = max(outcomes, key=lambda item: item[3])
        rows.append(
            {
                "Match": f"{home_name} - {away_name}",
                "Pronostic conseillé": f"{market_label}: {pick}" if pick != "Match nul" else "Match nul",
                "Code 1N2": code,
                "Probabilité retenue": probability,
                "Confiance": pred["confidence"],
                "Lecture": _confidence_label(pred["confidence"]),
                "Argument principal": _argument_sentence(home_name, away_name, pick, pred["confidence"], details),
            }
        )
    return pd.DataFrame(rows).sort_values(["Confiance", "Probabilité retenue"], ascending=False).head(limit)


def show():
    ui.page_hero(
        "Meilleurs pronostics",
        "Classez les meilleures lectures probables sur les équipes disponibles dans la base, avec un score de confiance lisible.",
    )

    leagues = prediction_helpers.fetch_leagues()
    if leagues.empty:
        st.warning("Aucune donnée disponible. Lancez d'abord une mise à jour.")
        return

    ui.section_label("Configuration")
    with st.container(border=True):
        league_map = {int(row.id): f"{row.name} — {row.country or ''}" for row in leagues.itertuples()}
        league_id = st.selectbox("Championnat", options=list(league_map.keys()), format_func=lambda key: league_map[key])
        available_seasons = prediction_helpers.fetch_seasons(league_id)
        season_options = sorted(prediction_helpers.configured_seasons(), reverse=True)
        selected_seasons = st.multiselect(
            "Saisons sportives",
            options=season_options,
            default=available_seasons[:3],
            format_func=season_period,
        )
        top_limit = st.segmented_control("Volume", options=[10, 20, 50], default=20)

    seasons_with_data, seasons_without_data = prediction_helpers.selected_season_status(selected_seasons, available_seasons)
    if seasons_without_data:
        st.warning(prediction_helpers.missing_seasons_message(seasons_without_data, seasons_with_data))
    matches_df = prediction_helpers.load_matches(league_id, seasons_with_data)
    team_options = prediction_helpers.fetch_teams(matches_df)
    if len(team_options) < 2:
        st.warning("Pas assez d'équipes disponibles pour générer un classement.")
        return
    st.info(prediction_helpers.teams_available_message(len(team_options), seasons_with_data))

    with st.container(border=True):
        selected_team_ids = st.multiselect(
            "Équipes à inclure",
            options=list(team_options.keys()),
            default=list(team_options.keys())[:12],
            format_func=lambda key: team_options[key],
        )
    selected_options = {team_id: team_options[team_id] for team_id in selected_team_ids}
    if len(selected_options) < 2:
        st.warning("Sélectionnez au moins deux équipes.")
        return

    if st.button("Générer le classement", type="primary", width="stretch"):
        rankings = _build_rankings(matches_df, selected_options, int(top_limit))
        ui.section_label("Classement")
        st.dataframe(_glossary_table(), hide_index=True, width="stretch")
        st.caption(
            "Le tableau classe les matchs simulés entre les équipes sélectionnées. "
            "La première ligne est le pronostic le plus fort selon les données disponibles."
        )
        st.dataframe(rankings, hide_index=True, width="stretch")

        if not rankings.empty:
            best = rankings.iloc[0]
            cols = st.columns(3)
            cols[0].metric("Meilleur pronostic", best["Pronostic conseillé"])
            cols[1].metric("Probabilité retenue", f"{best['Probabilité retenue']} %")
            cols[2].metric("Confiance", f"{best['Confiance']} %")
            st.success(
                f"Meilleur choix: {best['Pronostic conseillé']} sur {best['Match']} "
                f"({best['Confiance']} % de confiance). {best['Argument principal']}"
            )


if __name__ == "__main__":
    ui.run_direct_page("Meilleurs pronostics", show)
