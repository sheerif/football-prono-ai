"""Tableau de bord exclusivement consacré aux Expected Goals (xG)."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from sqlalchemy import text

from components import statistics_guide, ui
from database.database import engine
from services.season_format import season_period


def _load_leagues() -> pd.DataFrame:
    try:
        return pd.read_sql(
            text(
                """
                SELECT m.league_id AS id,
                       COALESCE(l.name, 'Championnat ' || m.league_id) AS name,
                       COALESCE(l.country, '') AS country,
                       COUNT(DISTINCT m.fixture_id) AS completed_matches,
                       COUNT(DISTINCT CASE
                           WHEN home_xg.expected_goals IS NOT NULL
                            AND away_xg.expected_goals IS NOT NULL
                           THEN m.fixture_id END) AS xg_matches
                FROM matches m
                LEFT JOIN leagues l ON l.id = m.league_id
                LEFT JOIN fixture_team_statistics home_xg
                  ON home_xg.fixture_id = m.fixture_id
                 AND home_xg.team_id = m.home_team_id
                LEFT JOIN fixture_team_statistics away_xg
                  ON away_xg.fixture_id = m.fixture_id
                 AND away_xg.team_id = m.away_team_id
                WHERE m.home_goals IS NOT NULL AND m.away_goals IS NOT NULL
                GROUP BY m.league_id, l.name, l.country
                ORDER BY name
                """
            ),
            engine,
        )
    except Exception:
        return pd.DataFrame(
            columns=["id", "name", "country", "completed_matches", "xg_matches"]
        )


def _load_seasons(league_id: int) -> list[int]:
    try:
        frame = pd.read_sql(
            text(
                """
                SELECT DISTINCT season
                FROM matches
                WHERE league_id = :league_id
                ORDER BY season DESC
                """
            ),
            engine,
            params={"league_id": int(league_id)},
        )
        return [int(value) for value in frame["season"].dropna().tolist()]
    except Exception:
        return []


def _load_scope(league_id: int, seasons: list[int]) -> pd.DataFrame:
    if not seasons:
        return pd.DataFrame()
    placeholders = ",".join(f":season_{index}" for index, _ in enumerate(seasons))
    params = {"league_id": int(league_id)}
    params.update(
        {f"season_{index}": int(season) for index, season in enumerate(seasons)}
    )
    try:
        frame = pd.read_sql(
            text(
                f"""
                SELECT m.fixture_id, m.league_id, m.season, m.date,
                       m.home_team_id, m.away_team_id,
                       home.name AS home_name, away.name AS away_name,
                       m.home_goals, m.away_goals, m.status,
                       home_xg.expected_goals AS home_xg,
                       away_xg.expected_goals AS away_xg,
                       home_xg.goals_prevented AS home_goals_prevented,
                       away_xg.goals_prevented AS away_goals_prevented,
                       home_xg.retrieved_at AS home_retrieved_at,
                       away_xg.retrieved_at AS away_retrieved_at,
                       home_xg.payload_sha256 AS home_payload_sha256,
                       away_xg.payload_sha256 AS away_payload_sha256,
                       home_xg.source_endpoint AS source_endpoint
                FROM matches m
                LEFT JOIN teams home ON home.id = m.home_team_id
                LEFT JOIN teams away ON away.id = m.away_team_id
                LEFT JOIN fixture_team_statistics home_xg
                  ON home_xg.fixture_id = m.fixture_id
                 AND home_xg.team_id = m.home_team_id
                LEFT JOIN fixture_team_statistics away_xg
                  ON away_xg.fixture_id = m.fixture_id
                 AND away_xg.team_id = m.away_team_id
                WHERE m.league_id = :league_id
                  AND m.season IN ({placeholders})
                  AND m.home_goals IS NOT NULL
                  AND m.away_goals IS NOT NULL
                ORDER BY m.date DESC, m.fixture_id DESC
                """
            ),
            engine,
            params=params,
        )
    except Exception:
        return pd.DataFrame()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce", utc=True)
    for column in ("home_goals", "away_goals", "home_xg", "away_xg"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["xg_complete"] = frame["home_xg"].notna() & frame["away_xg"].notna()
    return frame


def _coverage_by_season(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    grouped = (
        frame.groupby("season", dropna=True)
        .agg(matchs_termines=("fixture_id", "nunique"), matchs_xg=("xg_complete", "sum"))
        .reset_index()
    )
    grouped["couverture"] = (
        grouped["matchs_xg"] / grouped["matchs_termines"].clip(lower=1) * 100
    ).round(1)
    grouped["Saison sportive"] = grouped["season"].map(season_period)
    return grouped.sort_values("season")


def _team_view(frame: pd.DataFrame, team_id: int, limit: int) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    rows = frame[
        (frame["home_team_id"] == int(team_id))
        | (frame["away_team_id"] == int(team_id))
    ].copy()
    rows = rows[rows["xg_complete"]].sort_values("date", ascending=False).head(limit)
    result = []
    for row in rows.itertuples(index=False):
        is_home = int(row.home_team_id) == int(team_id)
        own_xg = float(row.home_xg if is_home else row.away_xg)
        opponent_xg = float(row.away_xg if is_home else row.home_xg)
        goals_for = float(row.home_goals if is_home else row.away_goals)
        goals_against = float(row.away_goals if is_home else row.home_goals)
        retrieved_at = row.home_retrieved_at if is_home else row.away_retrieved_at
        payload_hash = row.home_payload_sha256 if is_home else row.away_payload_sha256
        result.append(
            {
                "fixture_id": int(row.fixture_id),
                "Date": row.date,
                "Adversaire": row.away_name if is_home else row.home_name,
                "Lieu": "Domicile" if is_home else "Extérieur",
                "Buts": goals_for,
                "Buts encaissés": goals_against,
                "xG": own_xg,
                "xGA": opponent_xg,
                "Différentiel xG": round(own_xg - opponent_xg, 2),
                "Finition vs xG": round(goals_for - own_xg, 2),
                "Récupéré le": retrieved_at,
                "SHA-256": payload_hash,
            }
        )
    return pd.DataFrame(result)


def _team_rankings(frame: pd.DataFrame, minimum_matches: int = 3) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    complete = frame[frame["xg_complete"]]
    rows = []
    team_ids = sorted(
        set(complete["home_team_id"].dropna().astype(int))
        | set(complete["away_team_id"].dropna().astype(int))
    )
    for team_id in team_ids:
        view = _team_view(complete, team_id, limit=len(complete))
        if len(view) < minimum_matches:
            continue
        names = pd.concat(
            [
                complete.loc[complete["home_team_id"] == team_id, "home_name"],
                complete.loc[complete["away_team_id"] == team_id, "away_name"],
            ]
        ).dropna()
        rows.append(
            {
                "Équipe": str(names.iloc[0]) if not names.empty else f"Équipe {team_id}",
                "Matchs xG": len(view),
                "xG / match": round(view["xG"].mean(), 2),
                "xGA / match": round(view["xGA"].mean(), 2),
                "Différentiel xG": round(view["xG"].mean() - view["xGA"].mean(), 2),
                "Buts / match": round(view["Buts"].mean(), 2),
                "Finition vs xG": round(view["Finition vs xG"].mean(), 2),
            }
        )
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("Différentiel xG", ascending=False)


def _team_options(frame: pd.DataFrame) -> dict[int, str]:
    options: dict[int, str] = {}
    if frame.empty:
        return options
    for row in frame.itertuples(index=False):
        if pd.notna(row.home_team_id):
            options[int(row.home_team_id)] = str(row.home_name or row.home_team_id)
        if pd.notna(row.away_team_id):
            options[int(row.away_team_id)] = str(row.away_name or row.away_team_id)
    return dict(sorted(options.items(), key=lambda item: item[1]))


def _render_team(name: str, view: pd.DataFrame) -> None:
    st.markdown(f"### {name}")
    if view.empty:
        st.info("Aucun match avec les deux xG disponible dans cette sélection.")
        return
    metrics = st.columns(5)
    metrics[0].metric("Matchs couverts", len(view))
    metrics[1].metric("xG / match", f"{view['xG'].mean():.2f}")
    metrics[2].metric("xGA / match", f"{view['xGA'].mean():.2f}")
    metrics[3].metric(
        "Différentiel xG",
        f"{(view['xG'].mean() - view['xGA'].mean()):+.2f}",
    )
    metrics[4].metric("Finition vs xG", f"{view['Finition vs xG'].mean():+.2f}")

    chronological = view.sort_values("Date")
    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=chronological["Date"], y=chronological["xG"],
            mode="lines+markers", name="xG créés", line={"color": "#2aa198", "width": 3},
        )
    )
    figure.add_trace(
        go.Scatter(
            x=chronological["Date"], y=chronological["xGA"],
            mode="lines+markers", name="xG concédés", line={"color": "#d45a55", "width": 3},
        )
    )
    figure.update_layout(
        title=f"Évolution xG — {name}", xaxis_title="Match", yaxis_title="xG",
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        legend={"orientation": "h", "y": 1.05},
        margin={"l": 20, "r": 20, "t": 65, "b": 25},
    )
    figure.update_yaxes(gridcolor="rgba(10,34,57,0.10)")
    st.plotly_chart(figure, width="stretch")

    display = view.copy()
    display["Date"] = display["Date"].dt.strftime("%d/%m/%Y %H:%M UTC")
    st.dataframe(
        display[
            ["Date", "Adversaire", "Lieu", "Buts", "xG", "xGA", "Différentiel xG", "Finition vs xG"]
        ],
        hide_index=True,
        width="stretch",
    )


def show() -> None:
    ui.page_hero(
        "Expected Goals (xG)",
        "Explorez la qualité des occasions créées et concédées, leur couverture et leur provenance.",
    )
    st.info(
        "Cette page lit uniquement les xG déjà enregistrés dans Turso. "
        "Elle ne consomme aucune requête API-Football."
    )
    leagues = _load_leagues()
    if leagues.empty:
        st.warning("Aucun match terminé n’est disponible dans la base.")
        return

    ui.section_label("Périmètre xG")
    with st.container(border=True):
        labels = {
            int(row.id): f"{row.name} — {row.country}".strip(" —")
            for row in leagues.itertuples(index=False)
        }
        league_ids = list(labels)
        league_id = st.selectbox(
            "Championnat",
            league_ids,
            index=league_ids.index(61) if 61 in league_ids else 0,
            format_func=lambda value: labels[int(value)],
            key="xg_league",
        )
        available_seasons = _load_seasons(league_id)
        selected_seasons = st.multiselect(
            "Saisons sportives",
            available_seasons,
            default=available_seasons[:3],
            format_func=season_period,
            key="xg_seasons",
        )
        sample_limit = st.segmented_control(
            "Fenêtre récente par équipe",
            options=[5, 8, 15, 30],
            default=8,
            format_func=lambda value: f"{value} matchs",
            key="xg_window",
        )

    if not selected_seasons:
        st.warning("Sélectionnez au moins une saison sportive.")
        return
    progress = st.progress(0.0, text="0 % — Préparation des xG")
    progress.progress(0.35, text="35 % — Lecture des matchs et de leur provenance")
    frame = _load_scope(league_id, selected_seasons)
    progress.progress(0.7, text="70 % — Calcul de la couverture et des moyennes")
    if frame.empty:
        progress.progress(1.0, text="100 % — Aucune donnée dans ce périmètre")
        st.warning("Aucun match terminé pour cette sélection.")
        return

    complete = frame[frame["xg_complete"]]
    total = int(frame["fixture_id"].nunique())
    complete_count = int(complete["fixture_id"].nunique())
    partial_count = int(
        ((frame["home_xg"].notna()) ^ (frame["away_xg"].notna())).sum()
    )
    coverage = round(complete_count / max(1, total) * 100, 1)
    progress.progress(1.0, text="100 % — Analyse xG prête")

    ui.section_label("Couverture de la base")
    ui.kpi_grid(
        [
            {"label": "Matchs terminés", "value": total, "caption": "Périmètre sélectionné", "icon": "⚽"},
            {"label": "xG complets", "value": complete_count, "caption": "Deux équipes disponibles", "icon": "📈"},
            {"label": "Couverture", "value": f"{coverage} %", "caption": "Matchs exploitables", "icon": "✅"},
            {"label": "xG partiels", "value": partial_count, "caption": "Une équipe manque", "icon": "⚠️"},
        ],
        columns=4,
    )
    if coverage < 100:
        st.warning(
            f"{total - complete_count} match(s) n’ont pas encore deux valeurs xG. "
            "Utilisez « Mise à jour » pour poursuivre la synchronisation différentielle."
        )

    seasonal = _coverage_by_season(frame)
    if not seasonal.empty:
        coverage_chart = px.bar(
            seasonal,
            x="Saison sportive",
            y="couverture",
            text="couverture",
            labels={"couverture": "Couverture xG (%)"},
            title="Couverture xG par saison sportive",
            color_discrete_sequence=["#2aa198"],
        )
        coverage_chart.update_traces(texttemplate="%{text:.1f} %", textposition="outside")
        coverage_chart.update_yaxes(range=[0, 105], gridcolor="rgba(10,34,57,0.10)")
        coverage_chart.update_layout(
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            margin={"l": 20, "r": 20, "t": 55, "b": 25},
        )
        st.plotly_chart(coverage_chart, width="stretch")

    ui.section_label("Comparer les équipes")
    teams = _team_options(frame)
    selected_teams = st.multiselect(
        "Équipes à afficher (deux maximum)",
        list(teams),
        default=[],
        max_selections=2,
        format_func=lambda team_id: teams[team_id],
        key="xg_teams",
    )
    if not selected_teams:
        st.caption("Sélectionnez une ou deux équipes pour afficher leur évolution xG.")
    else:
        tabs = st.tabs([teams[team_id] for team_id in selected_teams])
        for tab, team_id in zip(tabs, selected_teams):
            with tab:
                _render_team(
                    teams[team_id],
                    _team_view(frame, team_id, int(sample_limit)),
                )

    ui.section_label("Classement xG du championnat")
    rankings = _team_rankings(frame)
    if rankings.empty:
        st.info("Au moins trois matchs xG par équipe sont nécessaires pour le classement.")
    else:
        ranking_chart = px.bar(
            rankings.head(20),
            x="Équipe",
            y="Différentiel xG",
            color="Différentiel xG",
            color_continuous_scale=["#d45a55", "#edf2f5", "#2aa198"],
            color_continuous_midpoint=0,
            title="Différentiel xG moyen par équipe",
        )
        ranking_chart.update_layout(
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            coloraxis_showscale=False, margin={"l": 20, "r": 20, "t": 55, "b": 80},
        )
        ranking_chart.update_yaxes(gridcolor="rgba(10,34,57,0.10)")
        st.plotly_chart(ranking_chart, width="stretch")
        st.dataframe(rankings, hide_index=True, width="stretch")

    ui.section_label("Définition et traçabilité")
    statistics_guide.render("xg", expanded=True)
    if complete.empty:
        st.info("Aucune preuve d’ingestion xG complète dans ce périmètre.")
    else:
        provenance = complete[
            [
                "fixture_id", "date", "home_name", "away_name",
                "home_xg", "away_xg", "source_endpoint",
                "home_retrieved_at", "home_payload_sha256", "away_payload_sha256",
            ]
        ].head(50).copy()
        provenance.columns = [
            "Fixture", "Date", "Domicile", "Extérieur", "xG domicile",
            "xG extérieur", "Endpoint", "Récupéré le", "SHA-256 domicile",
            "SHA-256 extérieur",
        ]
        with st.expander("Voir les 50 dernières preuves xG"):
            st.dataframe(provenance, hide_index=True, width="stretch")
            st.caption(
                "Chaque empreinte SHA-256 relie la valeur affichée à la réponse brute "
                "API-Football conservée dans la base."
            )


if __name__ == "__main__":
    ui.run_direct_page("xG", show)
