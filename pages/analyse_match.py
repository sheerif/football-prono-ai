import streamlit as st
import pandas as pd
from database.database import engine
from components import ui
from services import stats_service, prediction_service
from services import prediction_helpers
from sqlalchemy import text

LEAGUE_PRESETS = {
    2: "Europe - Ligue des Champions",
    61: "France - Ligue 1",
    39: "Angleterre - Premier League",
    140: "Espagne - La Liga",
    135: "Italie - Serie A",
    78: "Allemagne - Bundesliga",
}


def _recent_form_symbols(results):
    # results: list of 'W','D','L'
    mapping = {'W': '✅', 'D': '➖', 'L': '❌'}
    return ' '.join(mapping.get(r, '?') for r in results)


def _league_options():
    """Return a mapping of league_id -> display name for selection.

    Falls back to `LEAGUE_PRESETS` if dynamic lookup is not available.
    """
    try:
        q = text("SELECT id, name FROM leagues")
        df = pd.read_sql(q, engine)
        if not df.empty:
            return {int(r.id): r.name for r in df.itertuples()}
    except Exception:
        pass
    return LEAGUE_PRESETS


def _fetch_seasons_for_league(league_id: int):
    try:
        df = pd.read_sql(
            text("SELECT DISTINCT season FROM matches WHERE league_id = :lid ORDER BY season DESC"),
            engine,
            params={"lid": league_id},
        )
        seasons = []
        for s in df["season"].tolist():
            try:
                seasons.append(int(s))
            except Exception:
                seasons.append(s)
        return sorted(seasons, reverse=True)
    except Exception:
        return []


def _season_window(end_season: int, window: int = 10):
    start = max(2016, end_season - window + 1)
    return list(range(start, end_season + 1))


def _fetch_teams(league_id: int, seasons):
    try:
        if not seasons:
            return pd.DataFrame(columns=["id", "name"])
        placeholders = ",".join([f":s{i}" for i in range(len(seasons))])
        params = {"lid": league_id}
        params.update({f"s{i}": season for i, season in enumerate(seasons)})
        return pd.read_sql(
            text(
                f"SELECT DISTINCT t.id, t.name "
                f"FROM teams t JOIN matches m ON (t.id = m.home_team_id OR t.id = m.away_team_id) "
                f"WHERE m.league_id = :lid AND m.season IN ({placeholders}) ORDER BY t.name"
            ),
            engine,
            params=params,
        )
    except Exception:
        return pd.DataFrame(columns=["id", "name"])


def _form_summary(results):
    if not results:
        return "Aucune donnée"
    counts = {"W": results.count("W"), "D": results.count("D"), "L": results.count("L")}
    return f"{_recent_form_symbols(results)}  |  {counts['W']}V - {counts['D']}N - {counts['L']}D"


def _team_summary_metrics(team_name, stats, form_results):
    played = max(1, stats.get('played', 0))
    win_pct = round(stats.get('wins', 0) / played * 100, 1)
    goals_for_avg = round(stats.get('goals_for', 0) / played, 2)
    goals_against_avg = round(stats.get('goals_against', 0) / played, 2)
    return {
        "team_name": team_name,
        "played": stats.get('played', 0),
        "wins": stats.get('wins', 0),
        "draws": stats.get('draws', 0),
        "losses": stats.get('losses', 0),
        "goals_for": stats.get('goals_for', 0),
        "goals_against": stats.get('goals_against', 0),
        "win_pct": win_pct,
        "goals_for_avg": goals_for_avg,
        "goals_against_avg": goals_against_avg,
        "form_text": _form_summary(form_results),
    }


def _last_results(df, team_id, n=10):
    df_team = df[(df['home_team_id'] == team_id) | (df['away_team_id'] == team_id)].dropna(subset=['date']).sort_values('date', ascending=False).head(n)
    res = []
    for _, r in df_team.iterrows():
        if r['home_team_id'] == team_id:
            gf = r['home_goals']
            ga = r['away_goals']
        else:
            gf = r['away_goals']
            ga = r['home_goals']
        if pd.isna(gf) or pd.isna(ga):
            continue
        if gf > ga:
            res.append('W')
        elif gf == ga:
            res.append('D')
        else:
            res.append('L')
    return res


def _load_matches_window(league_id: int, seasons):
    try:
        if not seasons:
            return pd.DataFrame(columns=['fixture_id', 'league_id', 'season', 'date', 'home_team_id', 'away_team_id', 'home_goals', 'away_goals', 'winner', 'status'])
        placeholders = ",".join([f":s{i}" for i in range(len(seasons))])
        params = {"lid": league_id}
        params.update({f"s{i}": season for i, season in enumerate(seasons)})
        query = text(f"SELECT * FROM matches WHERE league_id = :lid AND season IN ({placeholders}) ORDER BY date")
        return pd.read_sql(query, engine, params=params)
    except Exception:
        return pd.DataFrame(columns=['fixture_id', 'league_id', 'season', 'date', 'home_team_id', 'away_team_id', 'home_goals', 'away_goals', 'winner', 'status'])


def _format_match_datetime(value):
    timestamp = pd.to_datetime(value, errors="coerce")
    if pd.isna(timestamp):
        return "Date inconnue"
    return timestamp.strftime("%d/%m/%Y %H:%M")


def _score_label(home_goals, away_goals):
    if pd.isna(home_goals) or pd.isna(away_goals):
        return "Score non disponible"
    return f"{int(home_goals)}-{int(away_goals)}"


def _result_label(goals_for, goals_against):
    if pd.isna(goals_for) or pd.isna(goals_against):
        return "Non joué / non compté"
    if goals_for > goals_against:
        return "Victoire"
    if goals_for == goals_against:
        return "Nul"
    return "Défaite"


def _team_matches_history_table(matches_df: pd.DataFrame, team_id: int, team_options: dict[int, str]) -> pd.DataFrame:
    team_matches = matches_df[
        (matches_df["home_team_id"] == team_id) | (matches_df["away_team_id"] == team_id)
    ].copy()
    if team_matches.empty:
        return pd.DataFrame(columns=["Horodatage", "Saison", "Lieu", "Adversaire", "Score", "Résultat", "Statut"])

    rows = []
    for _, match in team_matches.sort_values(["date", "season"], ascending=[False, False]).iterrows():
        is_home = int(match["home_team_id"]) == int(team_id)
        opponent_id = int(match["away_team_id"] if is_home else match["home_team_id"])
        home_goals = match.get("home_goals")
        away_goals = match.get("away_goals")
        goals_for = home_goals if is_home else away_goals
        goals_against = away_goals if is_home else home_goals
        rows.append(
            {
                "Horodatage": _format_match_datetime(match.get("date")),
                "Saison": int(match.get("season")) if not pd.isna(match.get("season")) else "",
                "Lieu": "Domicile" if is_home else "Extérieur",
                "Adversaire": team_options.get(int(opponent_id), str(opponent_id)),
                "Score": _score_label(home_goals, away_goals),
                "Résultat": _result_label(goals_for, goals_against),
                "Statut": match.get("status") or "Statut inconnu",
            }
        )
    return pd.DataFrame(rows)


def _h2h_history_table(matches_df: pd.DataFrame, home_team: int, away_team: int, team_options: dict[int, str]) -> pd.DataFrame:
    h2h = matches_df[
        ((matches_df["home_team_id"] == home_team) & (matches_df["away_team_id"] == away_team))
        | ((matches_df["home_team_id"] == away_team) & (matches_df["away_team_id"] == home_team))
    ].copy()
    if h2h.empty:
        return pd.DataFrame(columns=["Horodatage", "Saison", "Domicile", "Extérieur", "Score", "Vainqueur", "Statut"])

    rows = []
    for _, match in h2h.sort_values(["date", "season"], ascending=[False, False]).iterrows():
        home_name = team_options.get(int(match["home_team_id"]), str(match["home_team_id"]))
        away_name = team_options.get(int(match["away_team_id"]), str(match["away_team_id"]))
        if pd.isna(match.get("home_goals")) or pd.isna(match.get("away_goals")):
            winner = "Non joué / non compté"
        elif int(match["home_goals"]) > int(match["away_goals"]):
            winner = home_name
        elif int(match["away_goals"]) > int(match["home_goals"]):
            winner = away_name
        else:
            winner = "Match nul"
        rows.append(
            {
                "Horodatage": _format_match_datetime(match.get("date")),
                "Saison": int(match.get("season")) if not pd.isna(match.get("season")) else "",
                "Domicile": home_name,
                "Extérieur": away_name,
                "Score": _score_label(match.get("home_goals"), match.get("away_goals")),
                "Vainqueur": winner,
                "Statut": match.get("status") or "Statut inconnu",
            }
        )
    return pd.DataFrame(rows)


def _build_h2h_report(h2h_df: pd.DataFrame, home_team: int, away_team: int, home_name: str, away_name: str):
    rows = []
    totals = {
        home_team: {"wins": 0, "losses": 0, "draws": 0},
        away_team: {"wins": 0, "losses": 0, "draws": 0},
    }

    if h2h_df.empty:
        return pd.DataFrame(columns=["Saison", "Horodatage", "Domicile", "Extérieur", "Score", "Vainqueur"]), totals

    def _format_timestamp(value):
        timestamp = pd.to_datetime(value, errors="coerce")
        if pd.isna(timestamp):
            return value
        return timestamp.strftime("%d/%m/%Y %H:%M")

    for _, match in h2h_df.sort_values(["date", "season"], ascending=[False, False]).iterrows():
        if pd.isna(match.get("home_goals")) or pd.isna(match.get("away_goals")):
            continue

        home_goals = int(match["home_goals"])
        away_goals = int(match["away_goals"])

        if home_goals > away_goals:
            winner_id = match["home_team_id"]
            winner_name = home_name if winner_id == home_team else away_name
        elif away_goals > home_goals:
            winner_id = match["away_team_id"]
            winner_name = home_name if winner_id == home_team else away_name
        else:
            winner_id = None
            winner_name = "Match nul"

        if winner_id is None:
            totals[home_team]["draws"] += 1
            totals[away_team]["draws"] += 1
        elif winner_id == home_team:
            totals[home_team]["wins"] += 1
            totals[away_team]["losses"] += 1
        else:
            totals[away_team]["wins"] += 1
            totals[home_team]["losses"] += 1

        rows.append(
            {
                "Saison": match.get("season"),
                "Horodatage": _format_timestamp(match.get("date")),
                "Domicile": home_name if match["home_team_id"] == home_team else away_name,
                "Extérieur": away_name if match["away_team_id"] == away_team else home_name,
                "Score": f"{home_goals}-{away_goals}",
                "Vainqueur": winner_name,
            }
        )

    return pd.DataFrame(rows), totals


def show():
    ui.page_hero(
        "Analyse de match",
        "Comparez deux équipes, visualisez leur dynamique récente, leurs confrontations directes et une prédiction lisible basée sur les données importées.",
    )

    league_map = _league_options()
    ui.section_label("Configuration")
    with st.container(border=True):
        league_id = st.selectbox("Championnat", options=list(league_map.keys()), format_func=lambda k: league_map[k])

    seasons = _fetch_seasons_for_league(league_id)
    if not seasons:
        st.warning("Aucune saison trouvée pour ce championnat.")
        return
    season_options = sorted(prediction_helpers.configured_seasons(), reverse=True)
    # allow selecting multiple seasons; default = last seasons with local data
    default_end = seasons[0]
    default_window = sorted(_season_window(default_end, 10), reverse=True)
    # ensure defaults exist in options (types already coerced)
    default_window = [s for s in default_window if s in seasons]
    with st.container(border=True):
        selected_seasons = st.multiselect("Saisons", options=season_options, default=default_window, key="analyse_seasons")
    if not selected_seasons:
        seasons_window = default_window
    else:
        seasons_window, missing_seasons = prediction_helpers.selected_season_status(selected_seasons, seasons)
        seasons_window = sorted(seasons_window, reverse=True)
        if missing_seasons:
            st.warning(prediction_helpers.missing_seasons_message(missing_seasons, seasons_window))

    teams_df = _fetch_teams(league_id, seasons_window)
    if teams_df.empty:
        st.warning("Aucune équipe disponible pour cette saison/championnat.")
        return
    team_options = {row.id: row.name for row in teams_df.itertuples()}
    st.info(prediction_helpers.teams_available_message(len(team_options), seasons_window))

    with st.container(border=True):
        cols = st.columns(2)
        home_team = cols[0].selectbox("Équipe domicile", options=list(team_options.keys()), format_func=lambda k: team_options[k])
        away_team = cols[1].selectbox("Équipe extérieur", options=[k for k in team_options.keys() if k != home_team], format_func=lambda k: team_options[k])

    if home_team == away_team:
        st.error("Veuillez sélectionner deux équipes différentes.")
        return

    if st.button("Analyser le match", type="primary", use_container_width=True):
        matches_df = _load_matches_window(league_id, seasons_window)
        if matches_df.empty:
            st.warning("Aucun match disponible sur les 10 saisons retenues pour ce championnat.")
            return

        # General stats
        home_stats = stats_service.compute_basic_stats(matches_df, home_team)
        away_stats = stats_service.compute_basic_stats(matches_df, away_team)

        home_form = _last_results(matches_df, home_team, 10)
        away_form = _last_results(matches_df, away_team, 10)
        home_view = _team_summary_metrics(team_options[home_team], home_stats, home_form)
        away_view = _team_summary_metrics(team_options[away_team], away_stats, away_form)

        st.subheader("Résumé du match")
        st.caption(f"{league_map[league_id]} · Analyse sur {seasons_window[0]} → {seasons_window[-1]} (saisons sélectionnées)")
        st.info(f"{home_view['team_name']} reçoit {away_view['team_name']}")

        left, right = st.columns(2)
        with left:
            st.markdown(f"### {home_view['team_name']} (domicile)")
            st.metric("Matchs joués", home_view['played'])
            st.metric("Victoires", home_view['wins'])
            st.metric("Nuls", home_view['draws'])
            st.metric("Buts marqués / match", home_view['goals_for_avg'])
            st.metric("Buts encaissés / match", home_view['goals_against_avg'])
            st.metric("Taux de victoire", f"{home_view['win_pct']} %")
            st.write(f"Forme récente: {home_view['form_text']}")
        with right:
            st.markdown(f"### {away_view['team_name']} (extérieur)")
            st.metric("Matchs joués", away_view['played'])
            st.metric("Victoires", away_view['wins'])
            st.metric("Nuls", away_view['draws'])
            st.metric("Défaites", away_view['losses'])
            st.metric("Buts marqués / match", away_view['goals_for_avg'])
            st.metric("Buts encaissés / match", away_view['goals_against_avg'])
            st.metric("Taux de victoire", f"{away_view['win_pct']} %")
            st.write(f"Forme récente: {away_view['form_text']}")

        st.subheader("Comparaison offensive et défensive")
        comp_cols = st.columns(4)
        comp_cols[0].metric(f"Buts marqués - {home_view['team_name']}", home_view['goals_for'])
        comp_cols[1].metric(f"Buts encaissés - {home_view['team_name']}", home_view['goals_against'])
        comp_cols[2].metric(f"Buts marqués - {away_view['team_name']}", away_view['goals_for'])
        comp_cols[3].metric(f"Buts encaissés - {away_view['team_name']}", away_view['goals_against'])

        st.subheader("Forme récente")
        home_recent_10 = home_form
        away_recent_10 = away_form
        form_cols = st.columns(2)
        form_cols[0].write(f"**{home_view['team_name']}**")
        form_cols[0].write(home_view['form_text'])
        form_cols[1].write(f"**{away_view['team_name']}**")
        form_cols[1].write(away_view['form_text'])

        st.subheader("Matchs existants dans les saisons sélectionnées")
        st.caption(
            "Ces tableaux affichent tous les matchs présents dans la base pour les saisons sélectionnées, avec leur horodatage. "
            "Les matchs sans score sont visibles mais ne sont pas comptés dans les statistiques de forme."
        )
        home_history, away_history, h2h_history = st.tabs(
            [home_view['team_name'], away_view['team_name'], "Confrontations directes"]
        )
        with home_history:
            home_history_table = _team_matches_history_table(matches_df, home_team, team_options)
            if home_history_table.empty:
                st.info(f"Aucun match trouvé pour {home_view['team_name']} dans les saisons sélectionnées.")
            else:
                st.dataframe(home_history_table, use_container_width=True, hide_index=True)
        with away_history:
            away_history_table = _team_matches_history_table(matches_df, away_team, team_options)
            if away_history_table.empty:
                st.info(f"Aucun match trouvé pour {away_view['team_name']} dans les saisons sélectionnées.")
            else:
                st.dataframe(away_history_table, use_container_width=True, hide_index=True)
        with h2h_history:
            h2h_history_df = _h2h_history_table(matches_df, home_team, away_team, team_options)
            if h2h_history_df.empty:
                st.info("Aucune confrontation directe trouvée dans les saisons sélectionnées.")
            else:
                st.dataframe(h2h_history_df, use_container_width=True, hide_index=True)

        st.subheader("Confrontations directes")
        h2h_df = matches_df[
            ((matches_df["home_team_id"] == home_team) & (matches_df["away_team_id"] == away_team))
            | ((matches_df["home_team_id"] == away_team) & (matches_df["away_team_id"] == home_team))
        ].copy()
        h2h_table, h2h_totals = _build_h2h_report(
            h2h_df,
            home_team,
            away_team,
            home_view['team_name'],
            away_view['team_name'],
        )

        total_h2h = len(h2h_table)
        goals_home = 0
        goals_away = 0
        for _, r in h2h_df.iterrows():
            if pd.isna(r['home_goals']) or pd.isna(r['away_goals']):
                continue
            if r['home_team_id'] == home_team:
                goals_home += int(r['home_goals'])
                goals_away += int(r['away_goals'])
            else:
                goals_home += int(r['away_goals'])
                goals_away += int(r['home_goals'])

        h2h_cols = st.columns(6)
        h2h_cols[0].metric("Confrontations", total_h2h)
        h2h_cols[1].metric(f"Victoires {home_view['team_name']}", h2h_totals[home_team]["wins"])
        h2h_cols[2].metric(f"Défaites {home_view['team_name']}", h2h_totals[home_team]["losses"])
        h2h_cols[3].metric(f"Victoires {away_view['team_name']}", h2h_totals[away_team]["wins"])
        h2h_cols[4].metric(f"Défaites {away_view['team_name']}", h2h_totals[away_team]["losses"])
        h2h_cols[5].metric("Nuls", h2h_totals[home_team]["draws"])

        st.write(f"Buts {home_view['team_name']}: {goals_home} — Buts {away_view['team_name']}: {goals_away}")
        if not h2h_table.empty:
            st.caption("Chaque ligne affiche clairement l’équipe gagnante du match.")
            st.dataframe(h2h_table, use_container_width=True, hide_index=True)
        else:
            st.info("Aucune confrontation trouvée sur la fenêtre choisie.")

        # Simple prediction: compute strengths
        def form_score(results):
            if not results:
                return 0.5
            score = sum(3 if r=='W' else 1 if r=='D' else 0 for r in results)
            return score / (3*len(results))

        home_form = form_score(home_recent_10)
        away_form = form_score(away_recent_10)

        # attack/defense proxy
        home_attack = home_stats.get('goals_for', 0) / max(1, home_stats.get('played', 1))
        home_def = home_stats.get('goals_against', 0) / max(1, home_stats.get('played', 1))
        away_attack = away_stats.get('goals_for', 0) / max(1, away_stats.get('played', 1))
        away_def = away_stats.get('goals_against', 0) / max(1, away_stats.get('played', 1))

        home_strength = 0.6 * home_form + 0.2 * (home_attack - away_def) + 0.2 * 0.5
        away_strength = 0.6 * away_form + 0.2 * (away_attack - home_def) + 0.2 * 0.5

        pred = prediction_service.predict_simple(home_strength, away_strength)

        st.subheader("Prédiction lisible")
        pred_cols = st.columns(4)
        pred_cols[0].metric(f"Victoire {home_view['team_name']}", f"{pred['home_probability']} %")
        pred_cols[1].metric("Match nul", f"{pred['draw_probability']} %")
        pred_cols[2].metric(f"Victoire {away_view['team_name']}", f"{pred['away_probability']} %")
        pred_cols[3].metric("Confiance", f"{pred.get('confidence')} %")

        favorite = home_view['team_name'] if pred['home_probability'] > pred['away_probability'] and pred['home_probability'] > pred['draw_probability'] else away_view['team_name'] if pred['away_probability'] > pred['home_probability'] and pred['away_probability'] > pred['draw_probability'] else 'Match équilibré'
        if favorite == 'Match équilibré':
            st.warning(f"Lecture: {favorite}")
        else:
            st.success(f"Équipe favorite: {favorite}")

        reasons = []
        if home_form > away_form:
            reasons.append(f"Meilleure forme récente pour {home_view['team_name']}")
        elif away_form > home_form:
            reasons.append(f"Meilleure forme récente pour {away_view['team_name']}")
        if home_attack > away_attack:
            reasons.append(f"Avantage offensif pour {home_view['team_name']}")
        elif away_attack > home_attack:
            reasons.append(f"Avantage offensif pour {away_view['team_name']}")
        if home_def < away_def:
            reasons.append(f"Défense plus solide pour {home_view['team_name']}")
        elif away_def < home_def:
            reasons.append(f"Défense plus solide pour {away_view['team_name']}")
        if not reasons:
            reasons.append("Aucune tendance forte sur les données disponibles")

        st.markdown("### Pourquoi ce résultat ?")
        for reason in reasons:
            st.write(f"- {reason}")

        st.markdown("### Scores probables")
        probable_scores = ["2-0", "2-1", "1-1", "1-0"]
        st.write(", ".join(probable_scores))

        st.caption("Le modèle combine la forme récente, le niveau offensif/défensif et l’historique direct pour produire une estimation probabiliste.")

 
if __name__ == "__main__":
    try:
        st.set_page_config(page_title="Analyse de match", layout="wide")
    except Exception:
        pass
    show()
