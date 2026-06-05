import pandas as pd
import streamlit as st

from components import ui
from services import prediction_helpers
from services.season_format import season_period


def _stats_table(stats: dict) -> pd.DataFrame:
    labels = {
        "played": "Matchs joués",
        "wins": "Victoires",
        "draws": "Nuls",
        "losses": "Défaites",
        "goals_for": "Buts marqués",
        "goals_against": "Buts encaissés",
    }
    return pd.DataFrame(
        [{"Indicateur": labels.get(key, key), "Valeur": str(value)} for key, value in stats.items()]
    )


def _derived_stats_table(stats: dict, details: dict, side: str) -> pd.DataFrame:
    prefix = "home" if side == "home" else "away"
    played = max(1, stats["played"])
    return pd.DataFrame(
        [
            {"Indicateur": "Points de forme récente", "Valeur": f"{details[f'{prefix}_form_score']} / 100"},
            {"Indicateur": "Forme récente", "Valeur": prediction_helpers.format_form(details[f"{prefix}_form_results"])},
            {"Indicateur": "Buts marqués par match", "Valeur": str(round(stats["goals_for"] / played, 2))},
            {"Indicateur": "Buts encaissés par match", "Valeur": str(round(stats["goals_against"] / played, 2))},
            {"Indicateur": "Indice offensif utilisé", "Valeur": str(details[f"{prefix}_attack"])},
            {"Indicateur": "Indice défensif utilisé", "Valeur": str(details[f"{prefix}_defense"])},
            {"Indicateur": "Score de force du modèle", "Valeur": str(details[f"{prefix}_strength"])},
        ]
    )


def _explanation_table(details: dict) -> pd.DataFrame:
    return pd.DataFrame(
        [{"Facteur": factor, "Poids dans le calcul": weight} for factor, weight in details["weights"].items()]
    )


def _glossary_table() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Terme": "Probabilité",
                "Définition": "Estimation en pourcentage de chaque issue possible: victoire domicile, match nul ou victoire extérieur. Les trois probabilités totalisent environ 100 %.",
            },
            {
                "Terme": "Confiance",
                "Définition": "Pourcentage de l’issue la plus probable. Plus il est élevé, plus le modèle voit un scénario dominant; ce n’est pas une certitude.",
            },
        ]
    )


def _analysis_legend_table() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"Valeur": "Matchs joués", "Explication": "Nombre de matchs terminés avec score utilisés pour l’équipe dans la période sélectionnée."},
            {"Valeur": "Victoires / Nuls / Défaites", "Explication": "Bilan de l’équipe sur les matchs terminés sélectionnés."},
            {"Valeur": "Buts marqués", "Explication": "Total des buts inscrits par l’équipe sur les matchs analysés."},
            {"Valeur": "Buts encaissés", "Explication": "Total des buts reçus par l’équipe sur les matchs analysés."},
            {"Valeur": "Buts marqués par match", "Explication": "Moyenne offensive: buts marqués divisés par matchs joués."},
            {"Valeur": "Buts encaissés par match", "Explication": "Moyenne défensive: buts encaissés divisés par matchs joués. Plus c’est bas, mieux c’est."},
            {"Valeur": "Forme récente", "Explication": "Suite des derniers résultats: V = victoire, N = nul, D = défaite."},
            {"Valeur": "Points de forme récente", "Explication": "Score sur 100 basé sur les derniers résultats: victoire = 3 points, nul = 1 point, défaite = 0 point."},
            {"Valeur": "Indice offensif utilisé", "Explication": "Valeur de buts marqués par match injectée dans le modèle."},
            {"Valeur": "Indice défensif utilisé", "Explication": "Valeur de buts encaissés par match injectée dans le modèle."},
            {"Valeur": "Score de force du modèle", "Explication": "Score synthétique calculé avec la forme, l’attaque, la défense adverse et le contexte domicile/extérieur."},
            {"Valeur": "Probabilité", "Explication": "Chance estimée de chaque issue. Les trois issues totalisent environ 100 %."},
            {"Valeur": "Confiance", "Explication": "Probabilité de l’issue la plus forte. Ce n’est pas une garantie."},
        ]
    )


def _format_datetime(value):
    timestamp = pd.to_datetime(value, errors="coerce")
    if pd.isna(timestamp):
        return "Date inconnue"
    return timestamp.strftime("%d/%m/%Y %H:%M")


def _team_matches_table(matches_df: pd.DataFrame, team_id: int, team_options: dict[int, str]) -> pd.DataFrame:
    rows = []
    team_matches = matches_df[
        ((matches_df["home_team_id"] == team_id) | (matches_df["away_team_id"] == team_id))
        & matches_df["home_goals"].notna()
        & matches_df["away_goals"].notna()
    ].copy()
    if team_matches.empty:
        return pd.DataFrame(columns=["Horodatage", "Saison sportive", "Lieu", "Adversaire", "Score", "Résultat", "Statut"])

    team_matches = team_matches.sort_values(["date", "season"], ascending=[False, False])
    for _, match in team_matches.iterrows():
        is_home = int(match["home_team_id"]) == int(team_id)
        opponent_id = int(match["away_team_id"] if is_home else match["home_team_id"])
        opponent = team_options.get(opponent_id, str(opponent_id))
        home_goals = match.get("home_goals")
        away_goals = match.get("away_goals")

        home_goals = int(home_goals)
        away_goals = int(away_goals)
        score = f"{home_goals}-{away_goals}"
        goals_for = home_goals if is_home else away_goals
        goals_against = away_goals if is_home else home_goals
        if goals_for > goals_against:
            result = "Victoire"
        elif goals_for == goals_against:
            result = "Nul"
        else:
            result = "Défaite"

        rows.append(
            {
                "Horodatage": _format_datetime(match.get("date")),
                "Saison sportive": season_period(match.get("season")),
                "Lieu": "Domicile" if is_home else "Extérieur",
                "Adversaire": opponent,
                "Score": score,
                "Résultat": result,
                "Statut": match.get("status") or "Statut inconnu",
            }
        )
    return pd.DataFrame(rows)


def _head_to_head_table(matches_df: pd.DataFrame, home_team: int, away_team: int, team_options: dict[int, str]) -> pd.DataFrame:
    h2h = matches_df[
        ((matches_df["home_team_id"] == home_team) & (matches_df["away_team_id"] == away_team))
        | ((matches_df["home_team_id"] == away_team) & (matches_df["away_team_id"] == home_team))
    ].copy()
    h2h = h2h[h2h["home_goals"].notna() & h2h["away_goals"].notna()]
    if h2h.empty:
        return pd.DataFrame(columns=["Horodatage", "Saison sportive", "Domicile", "Extérieur", "Score", "Vainqueur", "Statut"])

    rows = []
    for _, match in h2h.sort_values(["date", "season"], ascending=[False, False]).iterrows():
        home_name = team_options.get(int(match["home_team_id"]), str(match["home_team_id"]))
        away_name = team_options.get(int(match["away_team_id"]), str(match["away_team_id"]))
        home_goals = int(match["home_goals"])
        away_goals = int(match["away_goals"])
        score = f"{home_goals}-{away_goals}"
        if home_goals > away_goals:
            winner = home_name
        elif away_goals > home_goals:
            winner = away_name
        else:
            winner = "Match nul"
        rows.append(
            {
                "Horodatage": _format_datetime(match.get("date")),
                "Saison sportive": season_period(match.get("season")),
                "Domicile": home_name,
                "Extérieur": away_name,
                "Score": score,
                "Vainqueur": winner,
                "Statut": match.get("status") or "Statut inconnu",
            }
        )
    return pd.DataFrame(rows)


def _match_context_table(league_name: str, selected_seasons, matches_df: pd.DataFrame, home_name: str, away_name: str) -> pd.DataFrame:
    completed = matches_df.dropna(subset=["home_goals", "away_goals"])
    return pd.DataFrame(
        [
            {"Information": "Championnat analysé", "Détail": league_name},
            {"Information": "Saisons sportives utilisées", "Détail": ", ".join(season_period(season) for season in selected_seasons)},
            {"Information": "Matchs du championnat dans la période", "Détail": str(len(matches_df))},
            {"Information": "Matchs terminés utilisés pour les statistiques", "Détail": str(len(completed))},
            {"Information": "Match demandé", "Détail": f"{home_name} reçoit {away_name}"},
        ]
    )


def _confidence_label(confidence: float) -> str:
    if confidence >= 65:
        return "signal fort"
    if confidence >= 55:
        return "avantage net, mais pas décisif"
    if confidence >= 45:
        return "match assez ouvert"
    return "signal faible"


def _favorite_sentence(favorite: str, confidence: float, home_name: str, away_name: str, details: dict) -> str:
    label = _confidence_label(confidence)
    if details["home_form_score"] > details["away_form_score"]:
        form_argument = f"la forme récente avantage {home_name} ({details['home_form_score']} contre {details['away_form_score']})"
    elif details["away_form_score"] > details["home_form_score"]:
        form_argument = f"la forme récente avantage {away_name} ({details['away_form_score']} contre {details['home_form_score']})"
    else:
        form_argument = f"la forme récente est équilibrée ({details['home_form_score']} chacun)"

    if details["home_attack"] > details["away_attack"]:
        attack_argument = f"{home_name} marque plus ({details['home_attack']} but(s)/match contre {details['away_attack']})"
    elif details["away_attack"] > details["home_attack"]:
        attack_argument = f"{away_name} marque plus ({details['away_attack']} but(s)/match contre {details['home_attack']})"
    else:
        attack_argument = f"les deux attaques sont au même niveau ({details['home_attack']} but(s)/match)"

    if details["home_strength"] > details["away_strength"]:
        strength_argument = f"le score global favorise {home_name} ({details['home_strength']} contre {details['away_strength']})"
    elif details["away_strength"] > details["home_strength"]:
        strength_argument = f"le score global favorise {away_name} ({details['away_strength']} contre {details['home_strength']})"
    else:
        strength_argument = f"le score global reste équilibré ({details['home_strength']} chacun)"

    if favorite == "Match nul":
        return (
            f"Le modèle penche légèrement vers un match nul ({confidence} % de confiance, {label}), car {form_argument}, "
            f"{attack_argument} et {strength_argument}."
        )
    return (
        f"Le modèle penche vers {favorite} ({confidence} % de confiance, {label}), car {form_argument}, "
        f"{attack_argument} et {strength_argument}."
    )


def _build_reasons(home_name: str, away_name: str, details: dict):
    reasons = []
    if details["home_form_score"] > details["away_form_score"]:
        reasons.append(
            f"Forme récente: avantage {home_name} ({details['home_form_score']} / 100 contre {details['away_form_score']} / 100)."
        )
    elif details["away_form_score"] > details["home_form_score"]:
        reasons.append(
            f"Forme récente: avantage {away_name} ({details['away_form_score']} / 100 contre {details['home_form_score']} / 100)."
        )
    else:
        reasons.append(f"Forme récente: équilibre total ({details['home_form_score']} / 100 chacun).")

    if details["home_attack"] > details["away_attack"]:
        reasons.append(
            f"Attaque: {home_name} produit plus ({details['home_attack']} but(s) par match contre {details['away_attack']})."
        )
    elif details["away_attack"] > details["home_attack"]:
        reasons.append(
            f"Attaque: {away_name} produit plus ({details['away_attack']} but(s) par match contre {details['home_attack']})."
        )
    else:
        reasons.append(f"Attaque: les deux équipes marquent au même rythme ({details['home_attack']} but(s) par match).")

    if details["home_defense"] < details["away_defense"]:
        reasons.append(
            f"Défense: {home_name} encaisse moins ({details['home_defense']} but(s) par match contre {details['away_defense']})."
        )
    elif details["away_defense"] < details["home_defense"]:
        reasons.append(
            f"Défense: {away_name} encaisse moins ({details['away_defense']} but(s) par match contre {details['home_defense']})."
        )
    else:
        reasons.append(f"Défense: même niveau mesuré ({details['home_defense']} but(s) encaissé(s) par match).")

    if details["home_strength"] > details["away_strength"]:
        reasons.append(
            f"Score final du modèle: {home_name} passe devant ({details['home_strength']} contre {details['away_strength']})."
        )
    elif details["away_strength"] > details["home_strength"]:
        reasons.append(
            f"Score final du modèle: {away_name} passe devant ({details['away_strength']} contre {details['home_strength']})."
        )
    else:
        reasons.append(f"Score final du modèle: égalité parfaite ({details['home_strength']} chacun).")
    return reasons


def show():
    ui.page_hero(
        "Prédiction IA",
        "Générez une lecture probabiliste simple à partir de la forme récente, de l’attaque, de la défense et de l’avantage domicile.",
    )

    leagues = prediction_helpers.fetch_leagues()
    if leagues.empty:
        st.warning("Aucune donnée disponible. Lancez d'abord un import dans Traitement des données.")
        return

    ui.section_label("Configuration")
    with st.container(border=True):
        league_map = {int(row.id): f"{row.name} — {row.country or ''}" for row in leagues.itertuples()}
        league_id = st.selectbox("Championnat", options=list(league_map.keys()), format_func=lambda key: league_map[key])
        available_seasons = prediction_helpers.fetch_seasons(league_id)
        season_options = sorted(prediction_helpers.configured_seasons(), reverse=True)
        default_seasons = available_seasons[:5]
        selected_seasons = st.multiselect(
            "Saisons sportives",
            options=season_options,
            default=default_seasons,
            format_func=season_period,
        )

    seasons_with_data, seasons_without_data = prediction_helpers.selected_season_status(selected_seasons, available_seasons)
    if seasons_without_data:
        st.warning(prediction_helpers.missing_seasons_message(seasons_without_data, seasons_with_data))
    matches_df = prediction_helpers.load_matches(league_id, seasons_with_data)
    team_options = prediction_helpers.fetch_teams(matches_df)
    if not team_options:
        st.warning("Aucune équipe disponible sur cette sélection.")
        return
    st.info(prediction_helpers.teams_available_message(len(team_options), seasons_with_data))

    with st.container(border=True):
        cols = st.columns(2)
        home_team = cols[0].selectbox("Équipe domicile", options=list(team_options.keys()), format_func=lambda key: team_options[key])
        away_team = cols[1].selectbox(
            "Équipe extérieur",
            options=[team_id for team_id in team_options if team_id != home_team],
            format_func=lambda key: team_options[key],
        )

    if st.button("Calculer la prédiction", type="primary", width="stretch"):
        pred, home_stats, away_stats, details = prediction_helpers.predict_match(matches_df, home_team, away_team)
        home_name = team_options[home_team]
        away_name = team_options[away_team]

        ui.section_label("Ce que ces informations représentent")
        st.dataframe(
            _match_context_table(league_map[league_id], seasons_with_data, matches_df, home_name, away_name),
            hide_index=True,
            width="stretch",
        )

        ui.section_label("Résultat")
        cols = st.columns(4)
        cols[0].metric(f"Victoire {home_name}", f"{pred['home_probability']} %")
        cols[1].metric("Match nul", f"{pred['draw_probability']} %")
        cols[2].metric(f"Victoire {away_name}", f"{pred['away_probability']} %")
        cols[3].metric("Confiance", f"{pred['confidence']} %")

        st.dataframe(_glossary_table(), hide_index=True, width="stretch")

        favorite = max(
            [
                (pred["home_probability"], home_name),
                (pred["draw_probability"], "Match nul"),
                (pred["away_probability"], away_name),
            ],
            key=lambda item: item[0],
        )[1]
        st.success(_favorite_sentence(favorite, pred["confidence"], home_name, away_name, details))

        ui.section_label("Pourquoi le modèle arrive à ce résultat ?")
        for reason in _build_reasons(home_name, away_name, details):
            st.write(f"- {reason}")

        ui.section_label("Méthode de calcul")
        st.dataframe(_explanation_table(details), hide_index=True, width="stretch")
        st.caption(
            "La prédiction est une estimation statistique interne. Elle compare les deux équipes dans la période sélectionnée; "
            "elle ne tient pas encore compte des blessures, suspensions, compositions probables, météo ou cotes de marché."
        )

        ui.section_label("Légende des valeurs analysées")
        st.dataframe(_analysis_legend_table(), hide_index=True, width="stretch")

        ui.section_label("Base statistique")
        stats_cols = st.columns(2)
        stats_cols[0].markdown(f"### {home_name}")
        stats_cols[0].dataframe(_stats_table(home_stats), hide_index=True, width="stretch")
        stats_cols[1].markdown(f"### {away_name}")
        stats_cols[1].dataframe(_stats_table(away_stats), hide_index=True, width="stretch")

        ui.section_label("Détails utilisés par le modèle")
        detail_cols = st.columns(2)
        detail_cols[0].markdown(f"### {home_name}")
        detail_cols[0].dataframe(_derived_stats_table(home_stats, details, "home"), hide_index=True, width="stretch")
        detail_cols[1].markdown(f"### {away_name}")
        detail_cols[1].dataframe(_derived_stats_table(away_stats, details, "away"), hide_index=True, width="stretch")

        ui.section_label("Matchs joués analysés avec horodatage")
        st.caption(
            "Ces tableaux listent les matchs terminés, avec score, présents dans la base pour les saisons sportives sélectionnées. "
            "Ce sont ces rencontres qui alimentent les statistiques ci-dessus."
        )
        home_matches, away_matches, h2h_matches = st.tabs([home_name, away_name, "Confrontations directes"])
        with home_matches:
            home_table = _team_matches_table(matches_df, home_team, team_options)
            if home_table.empty:
                st.info(f"Aucun match joué trouvé pour {home_name} dans cette sélection.")
            else:
                st.dataframe(home_table, hide_index=True, width="stretch")
        with away_matches:
            away_table = _team_matches_table(matches_df, away_team, team_options)
            if away_table.empty:
                st.info(f"Aucun match joué trouvé pour {away_name} dans cette sélection.")
            else:
                st.dataframe(away_table, hide_index=True, width="stretch")
        with h2h_matches:
            h2h_table = _head_to_head_table(matches_df, home_team, away_team, team_options)
            if h2h_table.empty:
                st.info("Aucune confrontation directe jouée dans les saisons sportives sélectionnées.")
            else:
                st.dataframe(h2h_table, hide_index=True, width="stretch")


if __name__ == "__main__":
    ui.run_direct_page("Prédiction IA", show)
