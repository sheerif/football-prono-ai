import pandas as pd
import streamlit as st
from sqlalchemy import text

from components import charts
from components import ui
from database.database import engine
from services import prediction_helpers, stats_service
from services.season_format import season_list, season_period, season_range


def _fetch_leagues():
    try:
        return pd.read_sql("SELECT id, name, country FROM leagues ORDER BY country, name", engine)
    except Exception:
        return pd.DataFrame(columns=["id", "name", "country"])


def _fetch_seasons(league_id: int):
    return prediction_helpers.fetch_seasons(league_id)


def _season_window(end_season: int, window: int = 10):
    start = max(2016, end_season - window + 1)
    return list(range(start, end_season + 1))


def _fetch_teams(league_id: int, season: int):
    try:
        return pd.read_sql(
            text(
                "SELECT DISTINCT t.id, t.name "
                "FROM teams t JOIN matches m ON (t.id = m.home_team_id OR t.id = m.away_team_id) "
                "WHERE m.league_id = :lid AND m.season = :season ORDER BY t.name"
            ),
            engine,
            params={"lid": league_id, "season": season},
        )
    except Exception:
        return pd.DataFrame(columns=["id", "name"])


def _fetch_teams_window(league_id: int, seasons):
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


def _load_matches_window(league_id: int, seasons):
    try:
        if not seasons:
            return pd.DataFrame(columns=["fixture_id", "league_id", "season", "date", "home_team_id", "away_team_id", "home_goals", "away_goals", "winner", "status"])
        placeholders = ",".join([f":s{i}" for i in range(len(seasons))])
        params = {"lid": league_id}
        params.update({f"s{i}": season for i, season in enumerate(seasons)})
        return pd.read_sql(
            text(f"SELECT * FROM matches WHERE league_id = :lid AND season IN ({placeholders}) ORDER BY date"),
            engine,
            params=params,
        )
    except Exception:
        return pd.DataFrame(columns=["fixture_id", "league_id", "season", "date", "home_team_id", "away_team_id", "home_goals", "away_goals", "winner", "status"])


def _recent_form(df: pd.DataFrame, team_id: int, n: int = 10):
    team_matches = df[(df["home_team_id"] == team_id) | (df["away_team_id"] == team_id)].copy()
    if team_matches.empty:
        return []
    team_matches = team_matches.dropna(subset=["date"]).sort_values("date", ascending=False).head(n)
    results = []
    for _, row in team_matches.iterrows():
        if row["home_team_id"] == team_id:
            gf = row["home_goals"]
            ga = row["away_goals"]
        else:
            gf = row["away_goals"]
            ga = row["home_goals"]
        if pd.isna(gf) or pd.isna(ga):
            continue
        if gf > ga:
            results.append("W")
        elif gf == ga:
            results.append("D")
        else:
            results.append("L")
    return results


def _result_counts(results):
    return {
        "wins": results.count("W"),
        "draws": results.count("D"),
        "losses": results.count("L"),
    }


def _format_form(results):
    labels = {"W": "V", "D": "N", "L": "D"}
    return " ".join(labels.get(result, result) for result in results[:10]) if results else "Aucune donnée"


def _last_h2h(matches_df: pd.DataFrame, team_a: int, team_b: int, n: int = 10):
    h2h = matches_df[
        ((matches_df["home_team_id"] == team_a) & (matches_df["away_team_id"] == team_b))
        | ((matches_df["home_team_id"] == team_b) & (matches_df["away_team_id"] == team_a))
    ].copy()
    if h2h.empty:
        return h2h
    return h2h.dropna(subset=["date"]).sort_values("date", ascending=False).head(n)


def _build_h2h_report(h2h_df: pd.DataFrame, team_a: int, team_b: int, name_a: str, name_b: str):
    rows = []
    totals = {
        team_a: {"wins": 0, "losses": 0, "draws": 0},
        team_b: {"wins": 0, "losses": 0, "draws": 0},
    }

    for _, row in h2h_df.sort_values(["date", "season"], ascending=[False, False]).iterrows():
        if pd.isna(row["home_goals"]) or pd.isna(row["away_goals"]):
            continue

        def _format_timestamp(value):
            timestamp = pd.to_datetime(value, errors="coerce")
            if pd.isna(timestamp):
                return value
            return timestamp.strftime("%d/%m/%Y %H:%M")

        if row["home_team_id"] == team_a:
            goals_a = int(row["home_goals"])
            goals_b = int(row["away_goals"])
        else:
            goals_a = int(row["away_goals"])
            goals_b = int(row["home_goals"])

        if goals_a > goals_b:
            winner = name_a
            totals[team_a]["wins"] += 1
            totals[team_b]["losses"] += 1
        elif goals_b > goals_a:
            winner = name_b
            totals[team_b]["wins"] += 1
            totals[team_a]["losses"] += 1
        else:
            winner = "Match nul"
            totals[team_a]["draws"] += 1
            totals[team_b]["draws"] += 1

        rows.append({
            "Saison sportive": season_period(row.get("season")),
            "Horodatage": _format_timestamp(row.get("date")),
            "Domicile": name_a if row["home_team_id"] == team_a else name_b,
            "Extérieur": name_b if row["away_team_id"] == team_b else name_a,
            "Score": f"{goals_a}-{goals_b}",
            "Vainqueur": winner,
        })

    return pd.DataFrame(rows), totals


def show():
    ui.page_hero(
        "Comparaison équipes",
        "Mettez deux équipes face à face sur les saisons importées: forme, volume offensif, solidité défensive et confrontations directes.",
    )

    leagues = _fetch_leagues()
    if leagues.empty:
        st.warning("Aucune donnée dans la base. Lancez d'abord le traitement des données.")
        return

    ui.section_label("Configuration")
    with st.container(border=True):
        league_map = {row.id: f"{row.name} — {row.country or ''}" for row in leagues.itertuples()}
        league_id = st.selectbox("Championnat", options=list(league_map.keys()), format_func=lambda k: league_map[k])

    seasons = _fetch_seasons(league_id)
    if not seasons:
        st.warning("Aucune saison sportive disponible pour ce championnat.")
        return
    season_options = sorted(prediction_helpers.configured_seasons(), reverse=True)
    # allow selecting multiple seasons; default = last seasons with local data
    default_end = seasons[0]
    default_window = sorted(_season_window(default_end, 10), reverse=True)
    # ensure defaults exist in options
    default_window = [s for s in default_window if s in seasons]
    with st.container(border=True):
        selected_seasons = st.multiselect(
            "Saisons sportives",
            options=season_options,
            default=default_window,
            format_func=season_period,
            key="compare_seasons",
        )
    if not selected_seasons:
        seasons_window = default_window
    else:
        seasons_window, missing_seasons = prediction_helpers.selected_season_status(selected_seasons, seasons)
        seasons_window = sorted(seasons_window, reverse=True)
        if missing_seasons:
            st.warning(prediction_helpers.missing_seasons_message(missing_seasons, seasons_window))

    teams_df = _fetch_teams_window(league_id, seasons_window)
    if teams_df.empty:
        st.warning("Aucune équipe disponible pour cette saison sportive.")
        return

    team_options = {row.id: row.name for row in teams_df.itertuples()}
    st.info(prediction_helpers.teams_available_message(len(team_options), seasons_window))
    with st.container(border=True):
        cols = st.columns(2)
        team_a = cols[0].selectbox("Équipe A", options=list(team_options.keys()), format_func=lambda k: team_options[k])
        team_b = cols[1].selectbox(
            "Équipe B",
            options=[team_id for team_id in team_options.keys() if team_id != team_a],
            format_func=lambda k: team_options[k],
        )

    if team_a == team_b:
        st.error("Choisissez deux équipes différentes.")
        return

    if st.button("Comparer les équipes", type="primary", width="stretch"):
        matches_df = _load_matches_window(league_id, seasons_window)

        stats_a = stats_service.compute_basic_stats(matches_df, team_a)
        stats_b = stats_service.compute_basic_stats(matches_df, team_b)
        form_a = _recent_form(matches_df, team_a, 10)
        form_b = _recent_form(matches_df, team_b, 10)
        h2h_df = matches_df[
            ((matches_df["home_team_id"] == team_a) & (matches_df["away_team_id"] == team_b))
            | ((matches_df["home_team_id"] == team_b) & (matches_df["away_team_id"] == team_a))
        ].copy()

        st.subheader("Indicateurs comparés")
        kpi_cols = st.columns(2)
        with kpi_cols[0]:
            st.markdown(f"### {team_options[team_a]}")
            st.metric("Matchs joués", stats_a["played"])
            st.metric("Victoires", stats_a["wins"])
            st.metric("Nuls", stats_a["draws"])
            st.metric("Défaites", stats_a["losses"])
            st.metric("Buts marqués", stats_a["goals_for"])
            st.metric("Buts encaissés", stats_a["goals_against"])
            st.metric("Forme récente", _format_form(form_a))
        with kpi_cols[1]:
            st.markdown(f"### {team_options[team_b]}")
            st.metric("Matchs joués", stats_b["played"])
            st.metric("Victoires", stats_b["wins"])
            st.metric("Nuls", stats_b["draws"])
            st.metric("Défaites", stats_b["losses"])
            st.metric("Buts marqués", stats_b["goals_for"])
            st.metric("Buts encaissés", stats_b["goals_against"])
            st.metric("Forme récente", _format_form(form_b))

        st.subheader("Comparaison radar")
        played_a = max(1, stats_a["played"])
        played_b = max(1, stats_b["played"])
        labels = ["Victoires", "Buts marqués", "Buts encaissés inversés", "Forme", "Expérience"]
        form_score_a = sum(3 if r == "W" else 1 if r == "D" else 0 for r in form_a) / max(1, 3 * len(form_a)) if form_a else 0
        form_score_b = sum(3 if r == "W" else 1 if r == "D" else 0 for r in form_b) / max(1, 3 * len(form_b)) if form_b else 0
        values_a = [
            stats_a["wins"],
            stats_a["goals_for"] / played_a,
            max(0, (stats_a["goals_against"] / played_a) * -1 + 10),
            form_score_a * 10,
            stats_a["played"],
        ]
        values_b = [
            stats_b["wins"],
            stats_b["goals_for"] / played_b,
            max(0, (stats_b["goals_against"] / played_b) * -1 + 10),
            form_score_b * 10,
            stats_b["played"],
        ]
        radar = charts.radar_team_comparison(labels, values_a, values_b, team_options[team_a], team_options[team_b])
        if radar is not None:
            st.plotly_chart(radar, width="stretch", key="radar_compare")

        st.subheader("Confrontations directes")
        if h2h_df.empty:
            st.info("Aucun face-à-face trouvé sur la fenêtre sélectionnée.")
        else:
            h2h_table, h2h_totals = _build_h2h_report(h2h_df, team_a, team_b, team_options[team_a], team_options[team_b])

            summary_cols = st.columns(6)
            summary_cols[0].metric("Confrontations", len(h2h_table))
            summary_cols[1].metric(f"Victoires {team_options[team_a]}", h2h_totals[team_a]["wins"])
            summary_cols[2].metric(f"Défaites {team_options[team_a]}", h2h_totals[team_a]["losses"])
            summary_cols[3].metric(f"Victoires {team_options[team_b]}", h2h_totals[team_b]["wins"])
            summary_cols[4].metric(f"Défaites {team_options[team_b]}", h2h_totals[team_b]["losses"])
            summary_cols[5].metric("Nuls", h2h_totals[team_a]["draws"])

            st.caption(f"Analyse des face-à-face sur {season_range(seasons_window)} : chaque ligne indique le vainqueur du match.")
            st.dataframe(h2h_table, width="stretch", hide_index=True)

        # Global info for selected seasons (league-wide)
        completed_matches_df = matches_df.dropna(subset=['home_goals', 'away_goals'])
        total_matches = len(completed_matches_df)
        if total_matches > 0:
            total_goals = int(completed_matches_df['home_goals'].sum() + completed_matches_df['away_goals'].sum())
            avg_goals = round(total_goals / total_matches, 2)
        else:
            total_goals = 0
            avg_goals = 0

        try:
            per_season = (
                completed_matches_df
                .groupby('season')
                .agg(
                    match_count=('fixture_id', 'size'),
                    home_goals=('home_goals', 'sum'),
                    away_goals=('away_goals', 'sum'),
                )
                .reset_index()
                .sort_values('season', ascending=False)
            )
            per_season["Buts"] = (per_season["home_goals"] + per_season["away_goals"]).astype(int)
            per_season["Buts / match"] = (per_season["Buts"] / per_season["match_count"]).round(2)
            per_season = per_season.rename(columns={"season": "Saison", "match_count": "Matchs terminés"})
            season_rows = [
                {
                    "season": int(row["Saison"]),
                    "matches": int(row["Matchs terminés"]),
                    "avg_goals": row["Buts / match"],
                }
                for _, row in per_season.iterrows()
            ]
            season_label = season_list(seasons_window)
            ui.season_summary(
                "Bilan du championnat",
                (
                    f"{league_map[league_id]} - saisons sportives: {season_label}. "
                    "Uniquement les matchs terminés avec un score renseigné."
                ),
                [
                    ("Matchs terminés", f"{total_matches:,}".replace(",", " ")),
                    ("Buts marqués", f"{total_goals:,}".replace(",", " ")),
                    ("Moyenne buts / match", avg_goals),
                ],
                season_rows,
            )
        except Exception:
            pass

        if st.checkbox("Afficher la liste complète des matchs du championnat sur la fenêtre sélectionnée"):
            # map team ids to names
            team_ids = pd.unique(matches_df[['home_team_id', 'away_team_id']].values.ravel('K'))
            team_ids = [int(t) for t in team_ids if pd.notna(t)]
            names_map = {}
            if team_ids:
                try:
                    q = text(f"SELECT id, name FROM teams WHERE id IN ({','.join([str(int(x)) for x in team_ids])})")
                    teams_names = pd.read_sql(q, engine)
                    names_map = {int(r.id): r.name for r in teams_names.itertuples()}
                except Exception:
                    names_map = {k: team_options.get(k, str(k)) for k in team_ids}

            rows = []
            for _, m in matches_df.sort_values(['season', 'date'], ascending=[False, False]).iterrows():
                if pd.isna(m.get('home_goals')) or pd.isna(m.get('away_goals')):
                    continue
                h = int(m['home_goals'])
                a = int(m['away_goals'])
                if h > a:
                    winner = names_map.get(int(m['home_team_id']), str(m['home_team_id']))
                    loser = names_map.get(int(m['away_team_id']), str(m['away_team_id']))
                elif a > h:
                    winner = names_map.get(int(m['away_team_id']), str(m['away_team_id']))
                    loser = names_map.get(int(m['home_team_id']), str(m['home_team_id']))
                else:
                    winner = 'Match nul'; loser = 'Aucun'
                try:
                    ts = pd.to_datetime(m.get('date'), errors='coerce')
                    tsf = ts.strftime('%d/%m/%Y %H:%M') if pd.notna(ts) else m.get('date')
                except Exception:
                    tsf = m.get('date')
                rows.append({
                    'Saison sportive': season_period(m.get('season')),
                    'Horodatage': tsf,
                    'Domicile': names_map.get(int(m['home_team_id']), str(m['home_team_id'])),
                    'Extérieur': names_map.get(int(m['away_team_id']), str(m['away_team_id'])),
                    'Score': f"{h}-{a}",
                    'Vainqueur': winner,
                    'Perdant': loser,
                })
            if rows:
                st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

        st.subheader("Lecture rapide")
        adv_a = stats_a["wins"] + stats_a["goals_for"] - stats_a["goals_against"]
        adv_b = stats_b["wins"] + stats_b["goals_for"] - stats_b["goals_against"]
        favorite = team_options[team_a] if adv_a > adv_b else team_options[team_b] if adv_b > adv_a else "Équilibré"
        st.write(f"Équipe la plus forte selon les données actuelles: **{favorite}**")


if __name__ == "__main__":
    ui.run_direct_page("Comparaison équipes", show)
 
