import pandas as pd
import streamlit as st
from sqlalchemy import text

from components import auth, sidebar, trends, ui
from database.database import engine
from services import background_jobs, import_service, player_service, schema_guard, trend_service
from services.season_format import season_period


def _team_options(league_id: int) -> dict[int, str]:
    try:
        frame = pd.read_sql(
            text(
                """
                SELECT DISTINCT t.id, t.name
                FROM teams t
                JOIN matches m
                  ON t.id = m.home_team_id OR t.id = m.away_team_id
                WHERE m.league_id = :league_id
                ORDER BY t.name
                """
            ),
            engine,
            params={"league_id": int(league_id)},
        )
        return {int(row.id): str(row.name) for row in frame.itertuples()}
    except Exception:
        return {}


def _league_controls(scopes: pd.DataFrame):
    leagues = (
        scopes[["league_id", "league_name"]]
        .drop_duplicates()
        .sort_values("league_name")
    )
    options = {
        f"{row.league_name} ({int(row.league_id)})": int(row.league_id)
        for row in leagues.itertuples()
    }
    labels = list(options)
    preferred = next(
        (label for label, league_id in options.items() if league_id == 61),
        labels[0],
    )
    label = st.selectbox(
        "Championnat",
        labels,
        index=labels.index(preferred),
        key="progression_league",
    )
    return options[label]


def _render_team_mode(league_id: int):
    teams = _team_options(league_id)
    if len(teams) < 2:
        st.info("Au moins deux équipes avec un historique sont nécessaires.")
        return
    ui.section_label("Comparer la progression de deux équipes")
    columns = st.columns(2)
    team_ids = list(teams)
    home_id = columns[0].selectbox(
        "Première équipe",
        team_ids,
        format_func=lambda team_id: teams[team_id],
        key="progression_team_a",
    )
    away_options = [team_id for team_id in team_ids if team_id != home_id]
    away_id = columns[1].selectbox(
        "Deuxième équipe",
        away_options,
        format_func=lambda team_id: teams[team_id],
        key="progression_team_b",
    )
    trends.render_team_comparison(
        trend_service.team_progression(home_id, league_id=league_id, limit=10),
        trend_service.team_progression(away_id, league_id=league_id, limit=10),
        teams[home_id],
        teams[away_id],
        f"progression_teams_{home_id}_{away_id}",
    )


def _render_player_mode(scopes: pd.DataFrame, league_id: int):
    seasons = sorted(
        scopes.loc[scopes["league_id"] == league_id, "season"].astype(int).unique(),
        reverse=True,
    )
    if not seasons:
        st.info("Aucune saison avec des joueurs n’est disponible.")
        return
    recommended = player_service.recommended_player_season(league_id, seasons)
    season = st.selectbox(
        "Effectif de départ",
        seasons,
        index=seasons.index(recommended) if recommended in seasons else 0,
        format_func=season_period,
        key="progression_player_season",
        help=(
            "Ce choix sert à retrouver le joueur. Sa courbe utilise ensuite ses "
            "10 dernières apparitions, même si elles traversent deux saisons."
        ),
    )
    players = player_service.load_players(league_id, int(season))
    if players.empty:
        st.info(
            "Aucun joueur n’est synchronisé pour cette saison. Utilisez la page "
            "Joueurs ou Mise à jour."
        )
        return
    players = players.drop_duplicates("player_id").sort_values(["team_name", "name"])
    options = {
        int(row.player_id): f"{row.name} · {row.team_name}"
        for row in players.itertuples()
    }
    selected_id = st.selectbox(
        "Joueur",
        list(options),
        format_func=lambda player_id: options[player_id],
        key="progression_player",
    )
    selected = players.loc[players["player_id"] == selected_id].iloc[0]
    profile, identity = st.columns([1, 4])
    with profile:
        if selected.get("photo"):
            st.image(selected["photo"], width=150)
    with identity:
        st.subheader(str(selected.get("name") or "Joueur"))
        st.caption(
            f"{selected.get('team_name') or 'Équipe'} · "
            f"{selected.get('games_position') or 'Poste non renseigné'}"
        )
        st.write(
            "La tendance utilise uniquement les performances détaillées par match "
            "déjà conservées dans la base."
        )
    trend = trend_service.player_progression(
        selected_id,
        league_id=league_id,
        limit=10,
    )
    trends.render_trend(
        trend,
        f"Progression de {selected.get('name') or 'ce joueur'}",
        f"progression_player_chart_{selected_id}",
    )


def show():
    ui.page_hero(
        "Progression",
        "Visualisez la progression ou la régression des équipes et des joueurs sur leurs 10 derniers matchs, y compris entre deux saisons.",
    )
    scopes = player_service.load_scopes()
    if scopes.empty:
        st.info("Aucun historique n’est disponible dans la base.")
        return
    with st.container(border=True):
        league_id = _league_controls(scopes)
        mode = st.radio(
            "Type d’analyse",
            ["Équipes", "Joueur"],
            horizontal=True,
            key="progression_mode",
        )
    if mode == "Équipes":
        _render_team_mode(league_id)
    else:
        _render_player_mode(scopes, league_id)


def run():
    try:
        st.set_page_config(page_title="Progression", layout="wide")
    except Exception:
        pass
    ui.inject_app_style()
    if not auth.is_authenticated():
        auth.login_page()
        st.stop()
    import_service.init_db()
    schema_guard.ensure_match_score_columns()
    background_jobs.start_startup_updates_once()
    sidebar.render_app_rail("Progression")
    with st.sidebar:
        st.caption(f"Connecté: {st.session_state.get('auth_user', 'utilisateur')}")
        auth.logout_button()
        ui.render_background_jobs()
    show()


if __name__ == "__main__":
    run()
