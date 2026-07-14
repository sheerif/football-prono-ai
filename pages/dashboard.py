import streamlit as st
import pandas as pd
from sqlalchemy import text
from database import models
from database.database import engine
from sqlalchemy.orm import Session
from components import charts
from components import ui
from services import import_service
from services.season_format import season_range


MATCH_COLUMNS = ["fixture_id", "league_id", "season", "date", "home_team_id", "away_team_id", "home_goals", "away_goals", "winner", "status"]


def _normalize_matches_df(df: pd.DataFrame) -> pd.DataFrame:
    for column in MATCH_COLUMNS:
        if column not in df.columns:
            df[column] = pd.NA
    for column in ["home_goals", "away_goals"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    return df


def _load_matches() -> pd.DataFrame:
    try:
        return _normalize_matches_df(pd.read_sql("SELECT * FROM matches", engine))
    except Exception:
        return pd.DataFrame(columns=MATCH_COLUMNS)


def _load_league_seasons() -> pd.DataFrame:
    try:
        return pd.read_sql("SELECT league_id, season FROM league_seasons", engine)
    except Exception:
        return pd.DataFrame(columns=["league_id", "season"])


def _season_scope(seasons) -> str:
    return season_range(seasons)


def _compute_kpis(matches_df: pd.DataFrame) -> dict:
    total_matches = len(matches_df)
    completed = matches_df.dropna(subset=["home_goals", "away_goals"]) if not matches_df.empty else matches_df
    completed_count = len(completed)

    if completed_count == 0:
        return {
            "avg_goals": 0,
            "home_win_rate": 0,
            "draw_rate": 0,
            "away_win_rate": 0,
            "btts_rate": 0,
            "over_25_rate": 0,
            "clean_sheet_rate": 0,
            "goals_total": 0,
            "completed_count": 0,
            "total_matches": total_matches,
        }

    goals_total = (completed["home_goals"].fillna(0) + completed["away_goals"].fillna(0)).sum()
    home_wins = (completed["home_goals"] > completed["away_goals"]).sum()
    away_wins = (completed["home_goals"] < completed["away_goals"]).sum()
    draws = (completed["home_goals"] == completed["away_goals"]).sum()
    btts = ((completed["home_goals"] > 0) & (completed["away_goals"] > 0)).sum()
    over_25 = ((completed["home_goals"].fillna(0) + completed["away_goals"].fillna(0)) > 2.5).sum()
    clean_sheets = ((completed["home_goals"] == 0) | (completed["away_goals"] == 0)).sum()

    return {
        "avg_goals": round(goals_total / completed_count, 2),
        "home_win_rate": round(home_wins / completed_count * 100, 1),
        "draw_rate": round(draws / completed_count * 100, 1),
        "away_win_rate": round(away_wins / completed_count * 100, 1),
        "btts_rate": round(btts / completed_count * 100, 1),
        "over_25_rate": round(over_25 / completed_count * 100, 1),
        "clean_sheet_rate": round(clean_sheets / completed_count * 100, 1),
        "goals_total": int(goals_total),
        "completed_count": completed_count,
        "total_matches": total_matches,
    }


def _top_leagues_table(matches_df: pd.DataFrame, league_seasons_df: pd.DataFrame) -> pd.DataFrame:
    if matches_df.empty and league_seasons_df.empty:
        return pd.DataFrame(columns=["Championnat", "Pays", "Saisons sportives", "Matchs importés", "Matchs joués"])
    try:
        leagues = pd.read_sql("SELECT id, name, country FROM leagues", engine)
    except Exception:
        leagues = pd.DataFrame(columns=["id", "name", "country"])

    if matches_df.empty:
        match_counts = pd.DataFrame(columns=["league_id", "matchs_importes", "matchs_joues"])
    else:
        match_counts = (
            matches_df.assign(match_joue=matches_df["home_goals"].notna() & matches_df["away_goals"].notna())
            .groupby("league_id")
            .agg(matchs_importes=("fixture_id", "count"), matchs_joues=("match_joue", "sum"))
            .reset_index()
        )
    if league_seasons_df.empty:
        season_counts = (
            matches_df.groupby("league_id")
            .agg(saison_min=("season", "min"), saison_max=("season", "max"))
            .reset_index()
        )
    else:
        season_counts = (
            league_seasons_df.groupby("league_id")
            .agg(saison_min=("season", "min"), saison_max=("season", "max"))
            .reset_index()
        )
    grouped = season_counts.merge(match_counts, on="league_id", how="left")
    grouped = grouped.merge(leagues, left_on="league_id", right_on="id", how="left")
    grouped["Championnat"] = grouped["name"].fillna(grouped["league_id"].apply(lambda value: f"Championnat {value}"))
    grouped["Pays"] = grouped["country"].fillna("Pays inconnu").replace("", "Pays inconnu")
    grouped["Saisons sportives"] = grouped.apply(lambda row: season_range([row.saison_min, row.saison_max]), axis=1)
    grouped["Matchs importés"] = grouped["matchs_importes"].fillna(0).astype(int)
    grouped["Matchs joués"] = grouped["matchs_joues"].fillna(0).astype(int)
    return grouped.sort_values("Matchs importés", ascending=False).head(10)[
        ["Championnat", "Pays", "Saisons sportives", "Matchs importés", "Matchs joués"]
    ]


def _quick_read_sentence(kpis: dict) -> str:
    return (
        f"Sur {kpis['completed_count']} matchs joués, les équipes à domicile gagnent {kpis['home_win_rate']} % du temps, "
        f"les matchs nuls représentent {kpis['draw_rate']} %, et les équipes à l’extérieur gagnent {kpis['away_win_rate']} %. "
        f"La base affiche aussi {kpis['avg_goals']} buts par match en moyenne."
    )


def _scope_table(matches_df: pd.DataFrame, league_seasons_df: pd.DataFrame) -> pd.DataFrame:
    completed = matches_df.dropna(subset=["home_goals", "away_goals"]) if not matches_df.empty else matches_df
    if league_seasons_df.empty:
        seasons = _season_scope(matches_df["season"]) if not matches_df.empty else "Aucune"
        league_count = matches_df["league_id"].nunique() if not matches_df.empty else 0
    else:
        seasons = _season_scope(league_seasons_df["season"])
        league_count = league_seasons_df["league_id"].nunique()
    return pd.DataFrame(
        [
            {"Information": "Ce que montre le tableau de bord", "Détail": "Une synthèse globale des matchs importés dans la base SQLite."},
            {"Information": "Saisons sportives couvertes", "Détail": seasons},
            {"Information": "Championnats suivis", "Détail": str(league_count)},
            {"Information": "Matchs terminés utilisés pour les pourcentages", "Détail": str(len(completed))},
            {"Information": "Accès API", "Détail": import_service.get_api_access_message()},
        ]
    )


def _indicator_glossary() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"Indicateur": "Championnats", "Définition": "Nombre de championnats enregistrés dans la base."},
            {"Indicateur": "Équipes", "Définition": "Nombre d’équipes enregistrées dans la base."},
            {"Indicateur": "Matchs", "Définition": "Nombre total de matchs importés, terminés ou non."},
            {"Indicateur": "Saisons sportives", "Définition": "Nombre de saisons sportives suivies dans la base, même si une saison en cours n’a pas encore de matchs importés."},
            {"Indicateur": "Matchs joués", "Définition": "Matchs avec un score domicile et extérieur disponible."},
            {"Indicateur": "Buts totaux", "Définition": "Somme des buts marqués sur les matchs joués."},
            {"Indicateur": "Moyenne buts / match", "Définition": "Buts totaux divisés par le nombre de matchs joués."},
            {"Indicateur": "Les deux équipes marquent", "Définition": "Part des matchs où les deux équipes ont marqué au moins un but."},
            {"Indicateur": "Plus de 2,5 buts", "Définition": "Part des matchs avec au moins 3 buts au total."},
            {"Indicateur": "Matchs sans encaisser", "Définition": "Part des matchs où au moins une équipe termine avec 0 but encaissé."},
        ]
    )


def show():
    matches_df = _load_matches()
    league_seasons_df = _load_league_seasons()
    kpis = _compute_kpis(matches_df)

    with Session(bind=engine) as session:
        leagues_count = session.query(models.League).count()
        teams_count = session.query(models.Team).count()
        matches_count = session.query(models.Match).count()
    seasons = league_seasons_df["season"].nunique() if not league_seasons_df.empty else (
        matches_df["season"].nunique() if not matches_df.empty else 0
    )

    if league_seasons_df.empty:
        season_scope = _season_scope(matches_df["season"]) if not matches_df.empty else "Aucune"
        league_scope = str(matches_df["league_id"].nunique()) if not matches_df.empty else "0"
    else:
        season_scope = _season_scope(league_seasons_df["season"])
        league_scope = str(league_seasons_df["league_id"].nunique())

    if matches_df.empty:
        st.warning("Aucune donnée disponible. Ouvrez 'Mise à jour' pour lancer l’import.")

    ui.dashboard_hero(
        "Prono insight",
        "Cockpit de lecture pour repérer les tendances fortes, cadrer les pronostics et contrôler la qualité des données importées.",
        [
            ("Championnats", f"{leagues_count:,}".replace(",", " ")),
            ("Équipes", f"{teams_count:,}".replace(",", " ")),
            ("Matchs", f"{matches_count:,}".replace(",", " ")),
            ("Saisons sportives", str(seasons)),
        ],
    )

    ui.dashboard_band(
        _quick_read_sentence(kpis),
        [
            ("Saisons sportives", season_scope),
            ("Championnats actifs", league_scope),
            ("Matchs terminés", f"{kpis['completed_count']:,}".replace(",", " ")),
            ("Accès API", import_service.get_api_access_message()),
        ],
    )

    ui.section_label("Indicateurs clés")
    ui.kpi_grid(
        [
            {
                "label": "Matchs joués",
                "value": f"{kpis['completed_count']:,}".replace(",", " "),
                "caption": "Matchs avec score complet",
                "accent": "#126447",
            },
            {
                "label": "Buts totaux",
                "value": f"{kpis['goals_total']:,}".replace(",", " "),
                "caption": "Somme domicile + extérieur",
                "accent": "#d8a528",
            },
            {
                "label": "Moyenne buts",
                "value": kpis["avg_goals"],
                "caption": "Buts par match terminé",
                "accent": "#4d7c8a",
            },
            {
                "label": "Les deux marquent",
                "value": f"{kpis['btts_rate']} %",
                "caption": "Signal utile BTTS",
                "accent": "#7a5c96",
            },
            {
                "label": "Plus de 2,5 buts",
                "value": f"{kpis['over_25_rate']} %",
                "caption": "Matchs à 3 buts ou plus",
                "accent": "#c94b3f",
            },
            {
                "label": "Clean sheet",
                "value": f"{kpis['clean_sheet_rate']} %",
                "caption": "Au moins une équipe à zéro",
                "accent": "#8a6f3e",
            },
        ]
    )

    ui.section_label("Répartition des résultats")
    res_df = pd.DataFrame(
        [
            {"result": "Victoires domicile", "count": (matches_df.dropna(subset=["home_goals", "away_goals"])["home_goals"] > matches_df.dropna(subset=["home_goals", "away_goals"])["away_goals"]).sum() if not matches_df.empty else 0},
            {"result": "Nuls", "count": (matches_df.dropna(subset=["home_goals", "away_goals"])["home_goals"] == matches_df.dropna(subset=["home_goals", "away_goals"])["away_goals"]).sum() if not matches_df.empty else 0},
            {"result": "Victoires extérieur", "count": (matches_df.dropna(subset=["home_goals", "away_goals"])["home_goals"] < matches_df.dropna(subset=["home_goals", "away_goals"])["away_goals"]).sum() if not matches_df.empty else 0},
        ]
    )
    fig_results = charts.pie_results(res_df)
    fig_matches = charts.bar_matches_by_season(matches_df)
    fig_goals = charts.line_goals_by_season(matches_df)

    chart_cols = st.columns(2)
    if fig_results is not None:
        chart_cols[0].plotly_chart(fig_results, width="stretch")
    if fig_matches is not None:
        chart_cols[1].plotly_chart(fig_matches, width="stretch")

    if fig_goals is not None:
        st.plotly_chart(fig_goals, width="stretch")

    ui.section_label("Tendances résultat")
    info_cols = st.columns(3)
    info_cols[0].metric("Victoires à domicile", f"{kpis['home_win_rate']} %")
    info_cols[1].metric("Matchs nuls", f"{kpis['draw_rate']} %")
    info_cols[2].metric("Victoires à l’extérieur", f"{kpis['away_win_rate']} %")

    top_leagues = _top_leagues_table(matches_df, league_seasons_df)
    if not top_leagues.empty:
        st.markdown("### Championnats les plus alimentés")
        st.caption("Ce tableau montre les championnats qui contiennent le plus de matchs dans la base, avec les saisons couvertes.")
        st.dataframe(top_leagues, width="stretch", hide_index=True)

    with st.expander("Voir les définitions des indicateurs"):
        st.dataframe(_indicator_glossary(), hide_index=True, width="stretch")

    st.caption("Les indicateurs sont calculés à partir des matchs présents dans SQLite. Si les données sont vides, allez dans 'Mise à jour'.")


if __name__ == "__main__":
    ui.run_direct_page("Prono insight", show)
