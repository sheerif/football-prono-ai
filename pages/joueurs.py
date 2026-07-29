import math

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from components import trends, ui
from services import player_service, trend_service
from services.season_format import season_period


POSITION_LABELS = {
    "Goalkeeper": "Gardien",
    "Defender": "Défenseur",
    "Midfielder": "Milieu",
    "Attacker": "Attaquant",
}

OUTFIELD_ANALYSIS_GROUPS = [
    (
        "Technique et influence",
        [
            {"label": "Note moyenne", "kind": "direct", "fields": ["games_rating"], "digits": 2},
            {"label": "Buts / 90", "kind": "per90", "fields": ["goals_total"], "digits": 2},
            {"label": "Passes déc. / 90", "kind": "per90", "fields": ["goals_assists"], "digits": 2},
            {"label": "Tirs cadrés", "kind": "ratio", "fields": ["shots_on"], "denominator": "shots_total", "digits": 0, "suffix": " %"},
            {"label": "Passes clés / 90", "kind": "per90", "fields": ["passes_key"], "digits": 2},
            {"label": "Précision passes", "kind": "direct", "fields": ["passes_accuracy"], "digits": 0, "suffix": " %"},
            {"label": "Dribbles réussis", "kind": "ratio", "fields": ["dribbles_success"], "denominator": "dribbles_attempts", "digits": 0, "suffix": " %"},
            {"label": "Fautes obtenues / 90", "kind": "per90", "fields": ["fouls_drawn"], "digits": 2},
        ],
    ),
    (
        "Engagement et maîtrise",
        [
            {"label": "Tacles / 90", "kind": "per90", "fields": ["tackles_total"], "digits": 2},
            {"label": "Interceptions / 90", "kind": "per90", "fields": ["tackles_interceptions"], "digits": 2},
            {"label": "Tirs bloqués / 90", "kind": "per90", "fields": ["tackles_blocks"], "digits": 2},
            {"label": "Duels gagnés", "kind": "ratio", "fields": ["duels_won"], "denominator": "duels_total", "digits": 0, "suffix": " %"},
            {"label": "Dribbles subis / 90", "kind": "per90", "fields": ["dribbles_past"], "digits": 2, "higher_is_better": False},
            {"label": "Fautes / 90", "kind": "per90", "fields": ["fouls_committed"], "digits": 2, "higher_is_better": False},
            {"label": "Cartons / 90", "kind": "per90", "fields": ["cards_yellow", "cards_red"], "digits": 2, "higher_is_better": False},
            {"label": "Taux de titularisation", "kind": "ratio", "fields": ["games_lineups"], "denominator": "games_appearences", "digits": 0, "suffix": " %"},
        ],
    ),
]

GOALKEEPER_ANALYSIS_GROUPS = [
    (
        "Performance du gardien",
        [
            {"label": "Note moyenne", "kind": "direct", "fields": ["games_rating"], "digits": 2},
            {"label": "Arrêts / 90", "kind": "per90", "fields": ["goals_saves"], "digits": 2},
            {"label": "Buts encaissés / 90", "kind": "per90", "fields": ["goals_conceded"], "digits": 2, "higher_is_better": False},
            {"label": "Penalties arrêtés", "kind": "direct", "fields": ["penalty_saved"], "digits": 0},
            {"label": "Précision passes", "kind": "direct", "fields": ["passes_accuracy"], "digits": 0, "suffix": " %"},
            {"label": "Passes / 90", "kind": "per90", "fields": ["passes_total"], "digits": 1},
            {"label": "Taux de titularisation", "kind": "ratio", "fields": ["games_lineups"], "denominator": "games_appearences", "digits": 0, "suffix": " %"},
            {"label": "Minutes / match", "kind": "ratio", "fields": ["games_minutes"], "denominator": "games_appearences", "scale": 1, "digits": 0},
        ],
    ),
    (
        "Maîtrise et discipline",
        [
            {"label": "Duels gagnés", "kind": "ratio", "fields": ["duels_won"], "denominator": "duels_total", "digits": 0, "suffix": " %"},
            {"label": "Interceptions / 90", "kind": "per90", "fields": ["tackles_interceptions"], "digits": 2},
            {"label": "Tirs bloqués / 90", "kind": "per90", "fields": ["tackles_blocks"], "digits": 2},
            {"label": "Fautes obtenues / 90", "kind": "per90", "fields": ["fouls_drawn"], "digits": 2},
            {"label": "Fautes / 90", "kind": "per90", "fields": ["fouls_committed"], "digits": 2, "higher_is_better": False},
            {"label": "Cartons / 90", "kind": "per90", "fields": ["cards_yellow", "cards_red"], "digits": 2, "higher_is_better": False},
        ],
    ),
]


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


def _metric_series(frame: pd.DataFrame, metric: dict) -> pd.Series:
    values = pd.Series(0.0, index=frame.index)
    for field in metric["fields"]:
        if field in frame:
            values = values.add(pd.to_numeric(frame[field], errors="coerce").fillna(0), fill_value=0)

    kind = metric["kind"]
    if kind == "direct":
        return values.where(values > 0)

    denominator_field = (
        "games_minutes" if kind == "per90" else metric.get("denominator")
    )
    denominator = pd.to_numeric(
        frame.get(denominator_field, pd.Series(0, index=frame.index)),
        errors="coerce",
    ).fillna(0)
    scale = float(metric.get("scale", 90 if kind == "per90" else 100))
    return (values * scale / denominator.where(denominator > 0)).replace(
        [float("inf"), float("-inf")], pd.NA
    )


def _metric_percentile(selected: pd.Series, peers: pd.DataFrame, metric: dict):
    peer_values = _metric_series(peers, metric).dropna()
    selected_value = _metric_series(
        pd.DataFrame([selected.to_dict()]), metric
    ).iloc[0]
    if pd.isna(selected_value) or peer_values.empty:
        return None

    if metric.get("higher_is_better", True):
        comparable = peer_values
        target = float(selected_value)
    else:
        comparable = -peer_values
        target = -float(selected_value)

    lower = int((comparable < target).sum())
    equal = int((comparable == target).sum())
    percentile = 100 * (lower + 0.5 * equal) / len(comparable)
    digits = int(metric.get("digits", 1))
    raw_value = f"{float(selected_value):.{digits}f}{metric.get('suffix', '')}"
    return {
        "label": metric["label"],
        "percentile": int(round(max(1, min(99, percentile)))),
        "raw_value": raw_value,
        "sample_size": int(len(comparable)),
    }


def _analysis_peers(selected: pd.Series, players: pd.DataFrame) -> pd.DataFrame:
    if players.empty:
        return players
    position = selected.get("games_position")
    peers = players[players["games_position"] == position].copy()
    minutes = pd.to_numeric(peers.get("games_minutes"), errors="coerce").fillna(0)
    established = peers[minutes >= 180]
    if len(established) >= 5:
        return established
    active = peers[minutes > 0]
    if len(active) >= 3:
        return active
    return players[pd.to_numeric(players.get("games_minutes"), errors="coerce").fillna(0) > 0]


def _percentile_color(value: int) -> str:
    if value >= 80:
        return "#18b981"
    if value >= 60:
        return "#84cc16"
    if value >= 40:
        return "#2aa198"
    return "#ef5b5b"


def _player_wheel(title: str, metrics: list[dict]):
    if not metrics:
        return None
    labels = [item["label"] for item in metrics]
    values = [item["percentile"] for item in metrics]
    raw_values = [item["raw_value"] for item in metrics]
    figure = go.Figure()
    figure.add_trace(
        go.Bar(
            x=values,
            y=labels,
            orientation="h",
            marker={
                "color": [_percentile_color(value) for value in values],
            },
            customdata=raw_values,
            hovertemplate=(
                "<b>%{y}</b><br>Score : %{x}/100"
                "<br>Valeur du joueur : %{customdata}<extra></extra>"
            ),
        )
    )
    figure.update_layout(
        title={"text": title, "x": 0.5, "xanchor": "center", "font": {"color": "#f8fafc", "size": 18}},
        height=max(300, 55 * len(metrics) + 85),
        margin={"l": 145, "r": 35, "t": 65, "b": 35},
        paper_bgcolor="#101827",
        plot_bgcolor="#101827",
        font={"family": "Inter, system-ui, sans-serif", "color": "#e5e7eb"},
        xaxis={
            "range": [0, 100],
            "title": "Score comparé (0 à 100)",
            "gridcolor": "rgba(255,255,255,0.14)",
            "zeroline": False,
        },
        yaxis={
            "autorange": "reversed",
            "gridcolor": "rgba(255,255,255,0.06)",
            "tickfont": {"size": 11, "color": "#f8fafc"},
        },
        showlegend=False,
    )
    return figure


def _trait_text(metric: dict, positive: bool) -> str:
    label = metric["label"]
    if positive:
        return f"{label} : supérieur à {metric['percentile']} % des joueurs comparables."
    return f"{label} : marge de progression par rapport aux joueurs du même poste."


def _render_visual_analysis(selected: pd.Series, players: pd.DataFrame):
    peers = _analysis_peers(selected, players)
    groups = (
        GOALKEEPER_ANALYSIS_GROUPS
        if selected.get("games_position") == "Goalkeeper"
        else OUTFIELD_ANALYSIS_GROUPS
    )
    scored_groups = []
    all_metrics = []
    for title, definitions in groups:
        scored = [
            result
            for metric in definitions
            if (result := _metric_percentile(selected, peers, metric)) is not None
        ]
        if scored:
            scored_groups.append((title, scored))
            all_metrics.extend(scored)

    ui.section_label("Statistiques du joueur")
    if len(all_metrics) < 4:
        st.info("Pas assez de statistiques pour afficher le profil du joueur.")
        return

    average = int(round(sum(item["percentile"] for item in all_metrics) / len(all_metrics)))
    sample_size = max(item["sample_size"] for item in all_metrics)
    ui.kpi_grid(
        [
            {
                "label": "Indice comparatif",
                "value": f"{average} / 100",
                "caption": "Moyenne des percentiles disponibles",
                "icon": "🎯",
            },
            {
                "label": "Référence",
                "value": f"{sample_size} joueurs",
                "caption": f"Même poste · minimum de temps de jeu",
                "icon": "👥",
            },
            {
                "label": "Données analysées",
                "value": f"{len(all_metrics)} indicateurs",
                "caption": "Valeurs ramenées à 90 minutes ou en pourcentage",
                "icon": "📊",
            },
        ]
    )
    st.caption(
        "Score sur 100 : plus la barre est longue, meilleur est le résultat comparé "
        "aux joueurs du même poste."
    )

    chart_columns = st.columns(len(scored_groups))
    for index, ((title, metrics), column) in enumerate(zip(scored_groups, chart_columns)):
        figure = _player_wheel(title, metrics)
        if figure is not None:
            column.plotly_chart(
                figure,
                width="stretch",
                config={"displayModeBar": False},
                key=f"player_wheel_{int(selected.get('player_id', 0))}_{index}",
            )

    ranked = sorted(all_metrics, key=lambda item: item["percentile"], reverse=True)
    strengths = ranked[: min(4, len(ranked))]
    improvements = list(reversed(ranked[-min(4, len(ranked)):]))
    strength_column, improvement_column = st.columns(2)
    with strength_column.container(border=True):
        st.markdown("### 🟢 Points forts")
        for metric in strengths:
            st.success(_trait_text(metric, positive=True))
    with improvement_column.container(border=True):
        st.markdown("### 🟠 Axes d’amélioration")
        for metric in improvements:
            st.warning(_trait_text(metric, positive=False))


def _metric_rows(groups: list[tuple[str, str, str | None]], row: pd.Series):
    units = {
        "Apparitions": " matchs",
        "Titularisations": " matchs",
        "Entrées": " fois",
        "Sorties": " fois",
        "Présences sur le banc": " fois",
        "Minutes": " min",
    }
    cards = []
    for label, field, suffix in groups:
        raw = row.get(field)
        value = _value(raw, 0)
        if isinstance(value, float) and math.isfinite(value) and value.is_integer():
            value = int(value)
        cards.append(
            {
                "label": label,
                "value": f"{value}{suffix or units.get(label, '')}",
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
                progress_bar = st.progress(
                    0.0,
                    text="0 % — Préparation…",
                )

                def update_progress(current, total, label):
                    ratio = min(1.0, current / max(1, total))
                    progress_bar.progress(
                        ratio,
                        text=ui.friendly_progress_message(label, ratio * 100),
                    )

                result = player_service.sync_players(
                    league_id,
                    season,
                    team_id=team_id,
                    progress_callback=update_progress,
                )
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
                ("Présences sur le banc", "substitutes_bench", None),
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

    player_kpis = [
            {
                "label": "Joueurs",
                "value": filtered["player_id"].nunique(),
                "caption": "Profils dans le filtre",
            },
            {
                "label": "Buts",
                "value": _integer(filtered["goals_total"].sum()),
                "caption": "Production offensive",
            },
            {
                "label": "Passes décisives",
                "value": _integer(filtered["goals_assists"].sum()),
                "caption": "Occasions converties",
            },
            {
                "label": "Minutes",
                "value": f"{_integer(filtered['games_minutes'].sum()):,}".replace(",", " "),
                "caption": "Temps de jeu cumulé",
            },
        ]
    try:
        ui.kpi_grid(player_kpis, columns=4)
    except TypeError:
        # Compatibilité avec un processus Streamlit ayant conservé l'ancienne
        # version de components.ui pendant un redéploiement à chaud.
        ui.kpi_grid(player_kpis)

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
    benchmark_players = player_service.load_players(league_id, season)
    _render_visual_analysis(selected, benchmark_players)
    _render_player_stats(selected)
    ui.section_label("Progression du joueur")
    st.caption("10 derniers matchs : la courbe montre si la forme monte ou baisse.")
    player_trend = trend_service.player_progression(
        int(selected["player_id"]), league_id=league_id, limit=10
    )
    trends.render_trend(
        player_trend,
        "Forme sur les 10 derniers matchs",
        f"joueur_progression_{int(selected['player_id'])}",
    )


if __name__ == "__main__":
    ui.run_direct_page("Joueurs", show)
