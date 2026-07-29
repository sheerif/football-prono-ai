import json
import math

import pandas as pd
import streamlit as st

from components import ui
from services import player_service
from services.season_format import season_period


POSITION_LABELS = {
    "Goalkeeper": "Gardien",
    "Defender": "Défenseur",
    "Midfielder": "Milieu",
    "Attacker": "Attaquant",
}


def _value(value, fallback="-"):
    if value is None:
        return fallback
    try:
        if pd.isna(value):
            return fallback
    except (TypeError, ValueError):
        pass
    return value


def _integer(value) -> int:
    try:
        if pd.isna(value):
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0


def _decimal(value, digits=2):
    try:
        if pd.isna(value):
            return "-"
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "-"


def _per_90(value, minutes):
    minutes = _integer(minutes)
    if minutes <= 0:
        return "-"
    return f"{(_integer(value) * 90 / minutes):.2f}"


def _metric_rows(groups: list[tuple[str, str, str | None]], row: pd.Series):
    cards = []
    for label, field, suffix in groups:
        raw = row.get(field)
        value = _value(raw, 0)
        if isinstance(value, float) and math.isfinite(value) and value.is_integer():
            value = int(value)
        cards.append(
            {
                "label": label,
                "value": f"{value}{suffix or ''}",
                "caption": None,
            }
        )
    ui.kpi_grid(cards)


def _scope_controls(scopes: pd.DataFrame):
    leagues = (
        scopes[["league_id", "league_name"]]
        .drop_duplicates()
        .sort_values("league_name")
    )
    league_options = {
        f"{row.league_name} ({int(row.league_id)})": int(row.league_id)
        for row in leagues.itertuples()
    }
    columns = st.columns(3)
    league_labels = list(league_options)
    preferred_label = next(
        (
            label
            for label, league_id in league_options.items()
            if int(league_id) == 61
        ),
        league_labels[0],
    )
    selected_league_label = columns[0].selectbox(
        "Compétition",
        league_labels,
        index=league_labels.index(preferred_label),
        key="players_league_select",
    )
    league_id = league_options[selected_league_label]

    season_values = sorted(
        scopes.loc[scopes["league_id"] == league_id, "season"].astype(int).unique(),
        reverse=True,
    )
    recommended_season = player_service.recommended_player_season(
        league_id, season_values
    )
    season_index = (
        season_values.index(recommended_season)
        if recommended_season in season_values
        else 0
    )
    season_key = f"players_season_{league_id}"
    season = columns[1].selectbox(
        "Saison",
        season_values,
        index=season_index,
        format_func=lambda value: season_period(value),
        key=season_key,
    )

    teams = player_service.load_teams(league_id, int(season))
    team_options = {"Toutes les équipes": None}
    for team in teams.itertuples():
        team_options[str(team.team_name)] = int(team.team_id)
    selected_team_label = columns[2].selectbox("Équipe", list(team_options))
    fallback_seasons = [value for value in season_values if value < int(season)]
    fallback_season = fallback_seasons[0] if fallback_seasons else None
    return (
        league_id,
        int(season),
        team_options[selected_team_label],
        selected_team_label,
        season_key,
        fallback_season,
    )


def _render_sync(
    league_id: int,
    season: int,
    team_id: int | None,
    team_label: str,
    season_key: str,
    fallback_season: int | None,
):
    scope = f"{team_label} · {season_period(season)}"
    with st.container(border=True):
        st.markdown("### Synchronisation API-Football")
        st.write(
            "Télécharge les profils et toutes les statistiques paginées disponibles "
            f"pour **{scope}**."
        )
        if team_id is None:
            st.caption(
                "Une compétition complète peut utiliser de nombreuses requêtes API "
                "(20 joueurs environ par page). Sélectionnez une équipe pour limiter le quota."
            )
        notice = st.session_state.pop("players_sync_notice", None)
        if notice:
            st.warning(notice)
        if st.button("Synchroniser les joueurs", type="primary", width="stretch"):
            try:
                with st.spinner("Téléchargement et enregistrement des joueurs…"):
                    result = player_service.sync_players(league_id, season, team_id=team_id)
                if result["profiles"] == 0:
                    if fallback_season is not None:
                        st.session_state[season_key] = int(fallback_season)
                        st.session_state["players_sync_notice"] = (
                            f"API-Football ne fournit encore aucun joueur pour "
                            f"{season_period(season)}. La page a été replacée sur "
                            f"{season_period(fallback_season)}, dernière saison exploitable."
                        )
                        st.rerun()
                    st.warning(
                        "API-Football ne fournit aucun joueur pour cette saison et ce périmètre."
                    )
                elif result["truncated"]:
                    st.warning(
                        f"{result['profiles']} profils enregistrés sur {result['pages']} pages. "
                        "La limite de sécurité de pagination a été atteinte."
                    )
                else:
                    st.success(
                        f"{result['profiles']} profils et {result['statistics']} lignes de "
                        f"statistiques enregistrés ({result['pages']} page(s) API)."
                    )
                if result["profiles"] > 0:
                    st.rerun()
            except Exception as exc:
                st.error(f"Synchronisation impossible : {exc}")


def _summary_table(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    summary = frame.copy()
    summary["Position"] = summary["games_position"].map(POSITION_LABELS).fillna(summary["games_position"])
    summary["Joueur"] = summary["name"]
    summary["Équipe"] = summary["team_name"]
    summary["Matchs"] = summary["games_appearences"].fillna(0).astype(int)
    summary["Titularisations"] = summary["games_lineups"].fillna(0).astype(int)
    summary["Minutes"] = summary["games_minutes"].fillna(0).astype(int)
    summary["Buts"] = summary["goals_total"].fillna(0).astype(int)
    summary["Passes décisives"] = summary["goals_assists"].fillna(0).astype(int)
    summary["Note"] = pd.to_numeric(summary["games_rating"], errors="coerce").round(2)
    summary["Cartons"] = (
        summary["cards_yellow"].fillna(0) + summary["cards_red"].fillna(0)
    ).astype(int)
    return summary[
        [
            "photo",
            "Joueur",
            "Équipe",
            "Position",
            "Matchs",
            "Titularisations",
            "Minutes",
            "Buts",
            "Passes décisives",
            "Note",
            "Cartons",
        ]
    ]


def _selected_player(
    frame: pd.DataFrame,
    selected_rows,
) -> pd.Series | None:
    if frame.empty or not selected_rows:
        return None
    try:
        position = int(selected_rows[0])
    except (TypeError, ValueError, IndexError):
        return None
    ordered = frame.reset_index(drop=True)
    if position < 0 or position >= len(ordered):
        return None
    return ordered.iloc[position]


def _render_profile(row: pd.Series):
    left, right = st.columns([1, 4])
    with left:
        photo = _value(row.get("photo"), None)
        if photo:
            st.image(photo, width=180)
        logo = _value(row.get("team_logo"), None)
        if logo:
            st.image(logo, width=70)
    with right:
        st.subheader(str(_value(row.get("name"), "Joueur")))
        st.caption(
            f"{_value(row.get('team_name'))} · "
            f"{POSITION_LABELS.get(row.get('games_position'), _value(row.get('games_position')))} · "
            f"{_value(row.get('league_name'))} {season_period(row.get('season'))}"
        )
        identity = st.columns(4)
        identity[0].metric("Âge", _value(row.get("age")))
        identity[1].metric("Nationalité", _value(row.get("nationality")))
        identity[2].metric("Taille", _value(row.get("height")))
        identity[3].metric("Poids", _value(row.get("weight")))
        birth_bits = [
            str(_value(row.get("birth_date"), "")),
            str(_value(row.get("birth_place"), "")),
            str(_value(row.get("birth_country"), "")),
        ]
        st.write("Naissance : " + " · ".join(bit for bit in birth_bits if bit))
        if row.get("injured") is True or row.get("injured") == 1:
            st.warning("Le joueur était signalé blessé lors de la dernière synchronisation.")


def _render_player_stats(row: pd.Series):
    overview, attack, passing, defense, discipline, all_stats = st.tabs(
        ["Vue d’ensemble", "Attaque", "Passes", "Défense", "Discipline", "Toutes les données"]
    )

    with overview:
        _metric_rows(
            [
                ("Apparitions", "games_appearences", None),
                ("Titularisations", "games_lineups", None),
                ("Minutes", "games_minutes", None),
                ("Note moyenne", "games_rating", None),
                ("Entrées", "substitutes_in", None),
                ("Sorties", "substitutes_out", None),
                ("Sur le banc", "substitutes_bench", None),
                ("Capitaine", "games_captain", None),
                ("Numéro", "games_number", None),
            ],
            row,
        )
        st.caption(
            f"Buts / 90 min : {_per_90(row.get('goals_total'), row.get('games_minutes'))} · "
            f"Passes décisives / 90 min : {_per_90(row.get('goals_assists'), row.get('games_minutes'))}"
        )

    with attack:
        _metric_rows(
            [
                ("Buts", "goals_total", None),
                ("Passes décisives", "goals_assists", None),
                ("Tirs", "shots_total", None),
                ("Tirs cadrés", "shots_on", None),
                ("Dribbles tentés", "dribbles_attempts", None),
                ("Dribbles réussis", "dribbles_success", None),
                ("Penalties marqués", "penalty_scored", None),
                ("Penalties manqués", "penalty_missed", None),
                ("Penalties obtenus", "penalty_won", None),
            ],
            row,
        )

    with passing:
        _metric_rows(
            [
                ("Passes", "passes_total", None),
                ("Passes clés", "passes_key", None),
                ("Précision", "passes_accuracy", " %"),
                ("Passes décisives", "goals_assists", None),
                ("Duels", "duels_total", None),
                ("Duels gagnés", "duels_won", None),
            ],
            row,
        )

    with defense:
        _metric_rows(
            [
                ("Tacles", "tackles_total", None),
                ("Interceptions", "tackles_interceptions", None),
                ("Tirs bloqués", "tackles_blocks", None),
                ("Dribbles subis", "dribbles_past", None),
                ("Buts encaissés", "goals_conceded", None),
                ("Arrêts", "goals_saves", None),
                ("Penalties arrêtés", "penalty_saved", None),
                ("Duels gagnés", "duels_won", None),
                ("Duels disputés", "duels_total", None),
            ],
            row,
        )

    with discipline:
        _metric_rows(
            [
                ("Fautes subies", "fouls_drawn", None),
                ("Fautes commises", "fouls_committed", None),
                ("Cartons jaunes", "cards_yellow", None),
                ("Jaune puis rouge", "cards_yellowred", None),
                ("Cartons rouges", "cards_red", None),
                ("Penalties concédés", "penalty_committed", None),
            ],
            row,
        )

    with all_stats:
        labels = {
            "games_appearences": "Apparitions",
            "games_lineups": "Titularisations",
            "games_minutes": "Minutes",
            "games_number": "Numéro",
            "games_position": "Position",
            "games_rating": "Note",
            "games_captain": "Capitaine",
            "substitutes_in": "Entrées en jeu",
            "substitutes_out": "Sorties",
            "substitutes_bench": "Présences sur le banc",
            "shots_total": "Tirs",
            "shots_on": "Tirs cadrés",
            "goals_total": "Buts",
            "goals_conceded": "Buts encaissés",
            "goals_assists": "Passes décisives",
            "goals_saves": "Arrêts",
            "passes_total": "Passes",
            "passes_key": "Passes clés",
            "passes_accuracy": "Précision des passes (%)",
            "tackles_total": "Tacles",
            "tackles_blocks": "Tirs bloqués",
            "tackles_interceptions": "Interceptions",
            "duels_total": "Duels",
            "duels_won": "Duels gagnés",
            "dribbles_attempts": "Dribbles tentés",
            "dribbles_success": "Dribbles réussis",
            "dribbles_past": "Dribbles subis",
            "fouls_drawn": "Fautes subies",
            "fouls_committed": "Fautes commises",
            "cards_yellow": "Cartons jaunes",
            "cards_yellowred": "Seconds jaunes",
            "cards_red": "Cartons rouges",
            "penalty_won": "Penalties obtenus",
            "penalty_committed": "Penalties concédés",
            "penalty_scored": "Penalties marqués",
            "penalty_missed": "Penalties manqués",
            "penalty_saved": "Penalties arrêtés",
        }
        values = [
            {"Statistique": label, "Valeur": str(_value(row.get(field)))}
            for field, label in labels.items()
        ]
        st.dataframe(pd.DataFrame(values), hide_index=True, width="stretch")
        with st.expander("Données JSON complètes conservées en base"):
            try:
                st.markdown("**Profil**")
                st.json(json.loads(row.get("profile_raw_json") or "{}"))
                st.markdown("**Statistiques de la saison**")
                st.json(json.loads(row.get("statistics_raw_json") or "{}"))
            except (TypeError, json.JSONDecodeError):
                st.code(str(row.get("profile_raw_json") or "{}"), language="json")
                st.code(str(row.get("statistics_raw_json") or "{}"), language="json")


def _render_history(player_id: int):
    history = player_service.load_player_history(player_id)
    if history.empty:
        return
    table = history.rename(
        columns={
            "season": "Saison",
            "league_name": "Compétition",
            "team_name": "Équipe",
            "games_position": "Poste",
            "games_appearences": "Matchs",
            "games_lineups": "Titularisations",
            "games_minutes": "Minutes",
            "games_rating": "Note",
            "goals_total": "Buts",
            "goals_assists": "Passes décisives",
            "cards_yellow": "Jaunes",
            "cards_red": "Rouges",
        }
    )
    table["Saison"] = table["Saison"].map(season_period)
    table["Poste"] = table["Poste"].map(POSITION_LABELS).fillna(table["Poste"])
    table["Note"] = pd.to_numeric(table["Note"], errors="coerce").round(2)
    with st.expander("Historique sur toutes les saisons synchronisées", expanded=False):
        st.dataframe(table, hide_index=True, width="stretch")


def show():
    ui.page_hero(
        "Joueurs",
        "Profils, performances et statistiques détaillées par compétition, équipe et saison.",
    )

    scopes = player_service.load_scopes()
    if scopes.empty:
        st.info("Aucune ligue ou saison n’est encore enregistrée. Lancez d’abord une mise à jour des données.")
        return

    with st.container(border=True):
        st.markdown("### Périmètre")
        (
            league_id,
            season,
            team_id,
            team_label,
            season_key,
            fallback_season,
        ) = _scope_controls(scopes)

    _render_sync(
        league_id,
        season,
        team_id,
        team_label,
        season_key,
        fallback_season,
    )
    frame = player_service.load_players(league_id, season, team_id=team_id)
    if frame.empty:
        st.info("Aucun joueur enregistré pour ce périmètre. Utilisez le bouton de synchronisation.")
        return

    ui.section_label("Effectif et recherche")
    filters = st.columns([2, 1])
    search = filters[0].text_input("Rechercher un joueur", placeholder="Nom ou prénom")
    positions = sorted(value for value in frame["games_position"].dropna().unique())
    selected_positions = filters[1].multiselect(
        "Poste",
        positions,
        format_func=lambda value: POSITION_LABELS.get(value, value),
    )
    filtered = frame
    if search:
        filtered = filtered[
            filtered["name"].fillna("").str.contains(search, case=False, regex=False)
            | filtered["firstname"].fillna("").str.contains(search, case=False, regex=False)
            | filtered["lastname"].fillna("").str.contains(search, case=False, regex=False)
        ]
    if selected_positions:
        filtered = filtered[filtered["games_position"].isin(selected_positions)]

    totals = st.columns(4)
    totals[0].metric("Joueurs", filtered["player_id"].nunique())
    totals[1].metric("Buts", _integer(filtered["goals_total"].sum()))
    totals[2].metric("Passes décisives", _integer(filtered["goals_assists"].sum()))
    totals[3].metric("Minutes", f"{_integer(filtered['games_minutes'].sum()):,}".replace(",", " "))

    st.caption(
        "Sélection interactive : cliquez directement sur une ligne pour afficher "
        "la fiche du joueur sous le tableau."
    )
    player_table = _summary_table(filtered).reset_index(drop=True)
    table_event = st.dataframe(
        player_table,
        hide_index=True,
        width="stretch",
        height=360,
        column_config={"photo": st.column_config.ImageColumn("Photo", width="small")},
        on_select="rerun",
        selection_mode="single-row",
        key="players_interactive_table",
    )
    if filtered.empty:
        st.warning("Aucun joueur ne correspond aux filtres.")
        return

    selected = _selected_player(filtered, table_event.selection.rows)
    ui.section_label("Fiche détaillée")
    if selected is None:
        st.info(
            "Cliquez sur la ligne d’un joueur dans le tableau pour afficher ici "
            "sa fiche complète et ses statistiques interactives."
        )
        return
    st.success(
        f"Joueur sélectionné : {selected.get('name', 'Joueur')} · "
        f"{selected.get('team_name', 'Équipe inconnue')}"
    )
    _render_profile(selected)
    _render_history(int(selected["player_id"]))
    _render_player_stats(selected)


if __name__ == "__main__":
    ui.run_direct_page("Joueurs", show)
