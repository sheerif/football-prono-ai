import inspect

import pandas as pd
import streamlit as st

from components import ranking_summary, tactical, ui
from services import analysis_store, cross_insight_service, final_prediction_service, lineup_service, prediction_helpers, ranking_service
from services.season_format import season_period


def _calculate_final_prediction(*args, match_date=None, **kwargs) -> dict:
    """Reste compatible avec un service encore en cache pendant le déploiement."""
    calculate = final_prediction_service.calculate
    try:
        parameters = inspect.signature(calculate).parameters
    except (TypeError, ValueError):
        parameters = {}
    if match_date is not None and (
        "match_date" in parameters
        or any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )
    ):
        kwargs["match_date"] = match_date
    final = calculate(*args, **kwargs)
    prediction = final.get("prediction") or {}
    if "ranking_score" not in prediction:
        final = dict(final)
        final["prediction"] = ranking_service.attach_ranking(prediction)
    return final


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
                "Terme": "Probabilité du scénario principal",
                "Définition": "Probabilité de l’issue la plus haute après combinaison éventuelle avec le conseil API.",
            },
            {
                "Terme": "Indice de solidité",
                "Définition": "Indice composite sur 100. Il ne représente pas une probabilité de victoire.",
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
            {"Valeur": "Probabilité du scénario principal", "Explication": "Probabilité de l’issue la plus haute après combinaison éventuelle avec le conseil API."},
            {"Valeur": "Indice de solidité", "Explication": "Indice de qualité sur 100 combinant probabilité, marge, données, stabilité et accord API. Ce n’est pas une probabilité."},
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
        return "écart élevé entre les scénarios"
    if confidence >= 55:
        return "écart modéré entre les scénarios"
    if confidence >= 45:
        return "scénarios encore ouverts"
    return "scénarios très proches"


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
            f"L’analyse place légèrement le match nul en tête ({confidence} % de probabilité, {label}), car {form_argument}, "
            f"{attack_argument} et {strength_argument}."
        )
    return (
        f"L’analyse place {favorite} en tête ({confidence} % de probabilité, {label}), car {form_argument}, "
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
    adjustment = details.get("player_adjustment")
    tactical_analysis = details.get("tactical_analysis") or {}
    if adjustment:
        reasons.append(
            "Composition prévue : "
            f"forme du onze {adjustment['home_player_form_score']} / 100 pour {home_name} "
            f"contre {adjustment['away_player_form_score']} / 100 pour {away_name}."
        )
        reasons.append(
            "Ajustement explicite : "
            f"{adjustment['player_probability_shift']:+.2f} point(s) pour la forme individuelle "
            f"et {adjustment['tactical_probability_shift']:+.2f} point(s) pour les dispositifs."
        )
    if tactical_analysis:
        reasons.append(
            f"Opposition tactique {tactical_analysis.get('home_formation')} / "
            f"{tactical_analysis.get('away_formation')} : "
            f"{tactical_analysis.get('structural_reading')}"
        )
    return reasons


def _show_match_prediction():
    st.subheader("Prédiction d'un match")
    st.caption(
        "Analysez une affiche à partir des résultats, du onze prévu, de la forme "
        "individuelle et de l’opposition des dispositifs."
    )

    leagues = prediction_helpers.fetch_leagues()
    if leagues.empty:
        st.warning("Aucune donnée disponible. Lancez d'abord une mise à jour.")
        return

    ui.section_label("Configuration")
    with st.container(border=True):
        league_map = {int(row.id): f"{row.name} — {row.country or ''}" for row in leagues.itertuples()}
        league_id = st.selectbox(
            "Championnat",
            options=list(league_map.keys()),
            index=prediction_helpers.default_league_index(league_map.keys()),
            format_func=lambda key: league_map[key],
            key="prediction_match_league",
        )
        available_seasons = prediction_helpers.fetch_seasons(league_id)
        season_options = sorted(prediction_helpers.configured_seasons(), reverse=True)
        default_seasons = available_seasons[:5]
        selected_seasons = st.multiselect(
            "Saisons sportives",
            options=season_options,
            default=default_seasons,
            format_func=season_period,
            key="prediction_match_seasons",
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
        home_team = cols[0].selectbox(
            "Équipe domicile",
            options=list(team_options.keys()),
            format_func=lambda key: team_options[key],
            key="prediction_match_home",
        )
        away_team = cols[1].selectbox(
            "Équipe extérieur",
            options=[team_id for team_id in team_options if team_id != home_team],
            format_func=lambda key: team_options[key],
            key="prediction_match_away",
        )

    if st.button(
        "Calculer la prédiction",
        type="primary",
        width="stretch",
        key="prediction_match_submit",
    ):
        analysis_season = lineup_service.resolve_player_season(
            [home_team, away_team], league_id, seasons_with_data
        )
        player_intelligence = lineup_service.get_prediction_intelligence(
            home_team_id=home_team,
            away_team_id=away_team,
            league_id=league_id,
            season=analysis_season,
        )
        home_name = team_options[home_team]
        away_name = team_options[away_team]
        api_signal = cross_insight_service.load_upcoming_api_signal(
            home_team,
            away_team,
        )
        final = _calculate_final_prediction(
            matches_df,
            home_team,
            away_team,
            home_name,
            away_name,
            player_intelligence=player_intelligence,
            api_signal=api_signal,
        )
        pred = final["prediction"]
        internal_prediction = final["internal_prediction"]
        home_stats = final["home_stats"]
        away_stats = final["away_stats"]
        details = final["model_details"]
        api_refinement = final["api_refinement"]
        consensus_advice = final["consensus_advice"]
        score_prediction = final["score_prediction"]
        cross_insight = cross_insight_service.build_cross_insight(
            matches_df=matches_df,
            home_team=home_team,
            away_team=away_team,
            home_name=home_name,
            away_name=away_name,
            prediction=internal_prediction,
            score_prediction=score_prediction,
            home_form_score=details["home_form_score"] / 100,
            away_form_score=details["away_form_score"] / 100,
            home_played=home_stats["played"],
            away_played=away_stats["played"],
            selected_seasons=seasons_with_data,
            api_signal=api_signal,
            player_intelligence=player_intelligence,
        )
        analysis_store.save_analysis_snapshot(
            analysis_type="prédiction_manuelle",
            league_id=league_id,
            season=analysis_season,
            home_team_id=home_team,
            away_team_id=away_team,
            prediction=pred,
            score_prediction=score_prediction,
            player_intelligence=player_intelligence,
            model_details=details,
            cross_insight=cross_insight,
            context={
                "selected_seasons": seasons_with_data,
                "home_name": home_name,
                "away_name": away_name,
                "historical_match_count": int(len(matches_df)),
            },
        )

        ui.section_label("Ce que ces informations représentent")
        st.dataframe(
            _match_context_table(league_map[league_id], seasons_with_data, matches_df, home_name, away_name),
            hide_index=True,
            width="stretch",
        )

        ui.section_label("Résultat")
        result_kpis = [
                {
                    "label": f"Victoire {home_name}",
                    "value": f"{pred['home_probability']} %",
                    "caption": "Scénario domicile",
                    "icon": "🏠",
                },
                {
                    "label": "Match nul",
                    "value": f"{pred['draw_probability']} %",
                    "caption": "Scénario équilibré",
                    "icon": "🤝",
                },
                {
                    "label": f"Victoire {away_name}",
                    "value": f"{pred['away_probability']} %",
                    "caption": "Scénario extérieur",
                    "icon": "✈️",
                },
                {
                    "label": "Probabilité scénario principal",
                    "value": f"{pred['confidence']} %",
                    "caption": "Probabilité de l’issue dominante",
                    "icon": "🎯",
                },
            ]
        try:
            ui.kpi_grid(result_kpis, columns=4)
        except TypeError:
            ui.kpi_grid(result_kpis)

        ranking_summary.render(pred)
        st.dataframe(_glossary_table(), hide_index=True, width="stretch")
        ui.render_api_refinement(api_refinement, consensus_advice)

        favorite = max(
            [
                (pred["home_probability"], home_name),
                (pred["draw_probability"], "Match nul"),
                (pred["away_probability"], away_name),
            ],
            key=lambda item: item[0],
        )[1]
        st.success(_favorite_sentence(favorite, pred["confidence"], home_name, away_name, details))
        ui.render_cross_insight(cross_insight)

        ui.section_label("Compositions et étude tactique")
        tactical.render_match_intelligence(
            player_intelligence,
            home_name,
            away_name,
        )

        ui.section_label("Pourquoi le modèle arrive à ce résultat ?")
        for reason in _build_reasons(home_name, away_name, details):
            st.write(f"- {reason}")

        ui.section_label("Méthode de calcul")
        st.dataframe(_explanation_table(details), hide_index=True, width="stretch")
        st.caption(
            "La prédiction est une estimation statistique interne. Elle compare les deux équipes dans la période sélectionnée; "
            "les compositions, blessures connues, forme individuelle et opposition tactique sont intégrées lorsqu’elles sont disponibles. "
            "La météo et les cotes de marché ne sont pas utilisées."
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


def _rankings_glossary_table() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Terme": "Scénario principal",
                "Définition": "Issue classée en tête par le modèle : victoire domicile, nul ou victoire extérieure.",
            },
            {
                "Terme": "Code 1N2",
                "Définition": "1 = domicile, N = match nul, 2 = extérieur.",
            },
            {
                "Terme": "Probabilité retenue",
                "Définition": "Pourcentage estimé pour le scénario principal.",
            },
            {
                "Terme": "Indice de solidité",
                "Définition": "Indice composite sur 100 fondé sur la probabilité principale, la marge, les données, la stabilité et l’accord API. Ce n’est pas une probabilité.",
            },
        ]
    )


def _ranking_confidence_label(confidence: float) -> str:
    if confidence >= 70:
        return "écart élevé"
    if confidence >= 60:
        return "écart modéré"
    if confidence >= 50:
        return "issues ouvertes"
    return "issues très proches"


def _ranking_argument(
    home_name: str,
    away_name: str,
    pick: str,
    details: dict,
) -> str:
    tactical_analysis = details.get("tactical_analysis") or {}
    tactical_edge = float(tactical_analysis.get("edge") or 0)
    if pick == "Match nul":
        return "Match serré : forces proches."
    if pick == home_name:
        if tactical_edge > 0.5:
            return (
                f"Avantage {home_name} : opposition tactique favorable "
                f"({tactical_analysis.get('home_formation')} / "
                f"{tactical_analysis.get('away_formation')})."
            )
        if details["home_form_score"] >= details["away_form_score"]:
            return f"Avantage {home_name} : meilleure forme."
        if details["home_attack"] >= details["away_attack"]:
            return f"Avantage {home_name} : attaque plus haute."
        return f"Avantage {home_name} : profil plus solide."
    if tactical_edge < -0.5:
        return (
            f"Avantage {away_name} : opposition tactique favorable "
            f"({tactical_analysis.get('away_formation')} / "
            f"{tactical_analysis.get('home_formation')})."
        )
    if details["away_form_score"] >= details["home_form_score"]:
        return f"Avantage {away_name} : meilleure forme."
    if details["away_attack"] >= details["home_attack"]:
        return f"Avantage {away_name} : attaque plus haute."
    return f"Avantage {away_name} : profil plus solide."


def _build_rankings(
    matches_df: pd.DataFrame,
    team_options: dict[int, str],
    limit: int,
    league_id: int,
    season: int,
    horizon_days: int = 60,
) -> pd.DataFrame:
    rows = []
    fixtures = prediction_helpers.upcoming_fixtures(
        matches_df,
        team_ids=team_options,
        days_ahead=horizon_days,
    )
    for match in fixtures.itertuples():
        home_team = int(match.home_team_id)
        away_team = int(match.away_team_id)
        fixture_id = int(match.fixture_id)
        fixture_season = int(match.season or season)
        historical_context = prediction_helpers.load_historical_context(
            int(match.league_id),
            match.date,
        )
        player_intelligence = lineup_service.get_match_intelligence(
            fixture_id=fixture_id,
            home_team_id=home_team,
            away_team_id=away_team,
            season=fixture_season,
            match_date=match.date,
        )
        api_signal = cross_insight_service.load_fixture_api_signal(fixture_id)
        home_name = team_options[home_team]
        away_name = team_options[away_team]
        final = _calculate_final_prediction(
            historical_context,
            home_team,
            away_team,
            home_name,
            away_name,
            player_intelligence=player_intelligence,
            api_signal=api_signal,
            match_date=match.date,
        )
        pred = final["prediction"]
        details = final["model_details"]
        api_refinement = final["api_refinement"]
        consensus_advice = final["consensus_advice"]
        outcomes = [
            ("1", "Victoire domicile", home_name, pred["home_probability"]),
            ("N", "Match nul", "Match nul", pred["draw_probability"]),
            ("2", "Victoire extérieure", away_name, pred["away_probability"]),
        ]
        code, market_label, pick, probability = max(
            outcomes, key=lambda item: item[3]
        )
        tactical_analysis = details.get("tactical_analysis") or {}
        adjustment = details.get("player_adjustment") or {}
        analysis_store.save_analysis_snapshot(
            analysis_type="classement_prédictif",
            league_id=league_id,
            season=fixture_season,
            home_team_id=home_team,
            away_team_id=away_team,
            fixture_id=fixture_id,
            prediction=pred,
            player_intelligence=player_intelligence,
            model_details=details,
            context={
                "home_name": home_name,
                "away_name": away_name,
                "kickoff": str(match.date),
                "historical_match_count": int(len(historical_context)),
            },
        )
        home_lineup = (player_intelligence or {}).get("home") or {}
        away_lineup = (player_intelligence or {}).get("away") or {}
        official_count = sum(
            bool(lineup.get("official"))
            for lineup in (home_lineup, away_lineup)
        )
        projected_count = sum(
            bool(lineup) and not bool(lineup.get("official"))
            for lineup in (home_lineup, away_lineup)
        )
        lineup_label = (
            "Officielles 2/2"
            if official_count == 2
            else f"Officielles {official_count}/2 · projections {projected_count}/2"
        )
        completeness = prediction_helpers.fixture_data_completeness(
            fixture_id,
            player_intelligence,
            len(historical_context),
        )
        rows.append(
            {
                "Date": _format_datetime(match.date),
                "Match": f"{home_name} - {away_name}",
                "Scénario principal": (
                    f"{market_label} : {pick}"
                    if pick != "Match nul"
                    else "Match nul"
                ),
                "Code 1N2": code,
                "Probabilité retenue": probability,
                "Indice de solidité": pred["ranking_score"],
                "Qualité des données": round(pred["data_quality"] * 100),
                "Marge": pred["margin"],
                "Compositions": lineup_label,
                "Complétude": f"{completeness['percentage']} %",
                "Données disponibles": completeness["label"],
                "Accord API": (
                    "Oui"
                    if api_refinement.get("agreement") is True
                    else "Non"
                    if api_refinement.get("agreement") is False
                    else "Indisponible"
                ),
                "Lecture affinée": consensus_advice["message"],
                "Historique": f"{len(historical_context)} matchs",
                "Dispositifs": (
                    f"{tactical_analysis.get('home_formation', '-')} / "
                    f"{tactical_analysis.get('away_formation', '-')}"
                ),
                "Impact composition": adjustment.get("probability_shift", 0),
                "Lecture": _ranking_confidence_label(pred["ranking_score"]),
                "Argument principal": _ranking_argument(
                    home_name, away_name, pick, details
                ),
            }
        )
    return (
        pd.DataFrame(rows)
        .sort_values(
            ["Indice de solidité", "Probabilité retenue"],
            ascending=False,
        )
        .head(limit)
    ) if rows else pd.DataFrame()


def _show_best_predictions():
    st.subheader("Scénarios des matchs programmés")
    st.caption(
        "Classez uniquement les rencontres réellement programmées dans la base. "
        "L’indice de solidité mesure la qualité du pronostic et non une probabilité."
    )

    leagues = prediction_helpers.fetch_leagues()
    if leagues.empty:
        st.warning("Aucune donnée disponible. Lancez d'abord une mise à jour.")
        return

    ui.section_label("Configuration")
    with st.container(border=True):
        league_map = {
            int(row.id): f"{row.name} — {row.country or ''}"
            for row in leagues.itertuples()
        }
        league_id = st.selectbox(
            "Championnat",
            options=list(league_map),
            index=prediction_helpers.default_league_index(league_map),
            format_func=lambda key: league_map[key],
            key="prediction_ranking_league",
        )
        available_seasons = prediction_helpers.fetch_seasons(league_id)
        selected_seasons = st.multiselect(
            "Saisons sportives",
            options=sorted(prediction_helpers.configured_seasons(), reverse=True),
            default=available_seasons[:3],
            format_func=season_period,
            key="prediction_ranking_seasons",
        )
        top_limit = st.segmented_control(
            "Volume",
            options=[10, 20, 50],
            default=20,
            key="prediction_ranking_limit",
        )
        horizon_days = st.segmented_control(
            "Horizon",
            options=[30, 60, 120],
            default=60,
            format_func=lambda days: f"{days} jours",
            key="prediction_ranking_horizon",
        )

    seasons_with_data, seasons_without_data = (
        prediction_helpers.selected_season_status(
            selected_seasons, available_seasons
        )
    )
    if seasons_without_data:
        st.warning(
            prediction_helpers.missing_seasons_message(
                seasons_without_data, seasons_with_data
            )
        )
    matches_df = prediction_helpers.load_matches(
        league_id, seasons_with_data
    )
    scheduled_fixtures = prediction_helpers.upcoming_fixtures(
        matches_df,
        days_ahead=int(horizon_days),
    )
    team_options = prediction_helpers.fetch_teams(scheduled_fixtures)
    if len(team_options) < 2:
        st.warning("Aucun match à venir n’est programmé pour cette sélection.")
        return

    with st.container(border=True):
        selected_team_ids = st.multiselect(
            "Équipes à inclure",
            options=list(team_options),
            default=list(team_options),
            format_func=lambda key: team_options[key],
            key="prediction_ranking_teams",
        )
        st.caption(
            f"{len(scheduled_fixtures)} rencontre(s) programmée(s) et "
            f"{len(team_options)} équipe(s) disponibles. Les deux équipes du "
            "match doivent être sélectionnées pour l’inclure."
        )

    selected_options = {
        team_id: team_options[team_id] for team_id in selected_team_ids
    }
    if len(selected_options) < 2:
        st.warning("Sélectionnez au moins deux équipes.")
        return

    if st.button(
        "Générer le classement",
        type="primary",
        width="stretch",
        key="prediction_ranking_submit",
    ):
        rankings = _build_rankings(
            matches_df,
            selected_options,
            int(top_limit),
            league_id=league_id,
            horizon_days=int(horizon_days),
            season=lineup_service.resolve_player_season(
                list(selected_options), league_id, seasons_with_data
            ),
        )
        ui.section_label("Classement")
        st.caption(
            "La première ligne correspond à l’indice de solidité le plus élevé "
            "parmi les matchs programmés sélectionnés."
        )
        st.dataframe(rankings, hide_index=True, width="stretch")
        with st.expander("Comprendre le classement"):
            st.dataframe(
                _rankings_glossary_table(),
                hide_index=True,
                width="stretch",
            )

        if not rankings.empty:
            best = rankings.iloc[0]
            ui.kpi_grid(
                [
                    {
                        "label": "Scénario classé en tête",
                        "value": best["Scénario principal"],
                        "caption": best["Match"],
                        "icon": "🏆",
                    },
                    {
                        "label": "Probabilité retenue",
                        "value": f"{best['Probabilité retenue']} %",
                        "caption": "Estimation interne",
                    },
                    {
                        "label": "Indice de solidité",
                        "value": f"{best['Indice de solidité']} / 100",
                        "caption": "Indice composite, pas une probabilité",
                    },
                ]
            )
            st.info(
                f"Scénario classé en tête : {best['Scénario principal']} sur "
                f"{best['Match']} (indice {best['Indice de solidité']} / 100). "
                f"{best['Argument principal']}"
            )


def show():
    ui.page_hero(
        "Prédictions",
        "Analysez un match ou comparez les rencontres programmées avec compositions clairement identifiées et lecture tactique.",
    )
    match_tab, ranking_tab = st.tabs(
        ["Prédiction d'un match", "Matchs programmés"]
    )
    with match_tab:
        _show_match_prediction()
    with ranking_tab:
        _show_best_predictions()


if __name__ == "__main__":
    ui.run_direct_page("Prédictions", show)
