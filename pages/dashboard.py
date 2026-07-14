import streamlit as st
import pandas as pd
from sqlalchemy import text
from database.database import engine
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


def _format_int(value) -> str:
    try:
        return f"{int(value):,}".replace(",", " ")
    except Exception:
        return "0"


def _format_percent(value) -> str:
    try:
        return f"{float(value):.1f} %"
    except Exception:
        return "0.0 %"


def _load_data_health() -> dict:
    queries = {
        "upcoming": """
            SELECT COUNT(*)
            FROM matches
            WHERE date >= CURRENT_TIMESTAMP
              AND home_goals IS NULL
              AND away_goals IS NULL
        """,
        "fixture_details": "SELECT COUNT(*) FROM fixture_api_details",
        "fixture_predictions": "SELECT COUNT(*) FROM fixture_api_predictions",
        "preview_cache": "SELECT COUNT(*) FROM fixture_match_previews",
    }
    health = {}
    with engine.begin() as conn:
        for key, query in queries.items():
            try:
                health[key] = int(conn.execute(text(query)).scalar() or 0)
            except Exception:
                health[key] = 0
    return health


def _upcoming_by_league() -> pd.DataFrame:
    try:
        rows = pd.read_sql(
            text(
                """
                SELECT
                    COALESCE(l.name, 'Championnat ' || m.league_id) AS Championnat,
                    COUNT(*) AS "Matchs à venir",
                    MIN(m.date) AS "Prochain match"
                FROM matches m
                LEFT JOIN leagues l ON l.id = m.league_id
                WHERE m.date >= CURRENT_TIMESTAMP
                  AND m.home_goals IS NULL
                  AND m.away_goals IS NULL
                GROUP BY m.league_id, l.name
                ORDER BY "Matchs à venir" DESC, "Prochain match"
                LIMIT 8
                """
            ),
            engine,
        )
    except Exception:
        return pd.DataFrame(columns=["Championnat", "Matchs à venir", "Prochain match"])
    if not rows.empty:
        rows["Prochain match"] = pd.to_datetime(rows["Prochain match"], errors="coerce").dt.strftime("%d/%m/%Y %H:%M")
        rows["Prochain match"] = rows["Prochain match"].fillna("Date inconnue")
    return rows


def _league_readiness_table(matches_df: pd.DataFrame, league_seasons_df: pd.DataFrame) -> pd.DataFrame:
    table = _top_leagues_table(matches_df, league_seasons_df)
    if table.empty:
        return table
    table = table.copy()
    table["Taux joué"] = table.apply(
        lambda row: _format_percent(row["Matchs joués"] / row["Matchs importés"] * 100) if row["Matchs importés"] else "0.0 %",
        axis=1,
    )
    return table[["Championnat", "Saisons sportives", "Matchs importés", "Matchs joués", "Taux joué"]]


def _next_action(health: dict, matches_df: pd.DataFrame) -> str:
    if matches_df.empty:
        return "Importer les championnats dans Mise à jour."
    if health["upcoming"] == 0:
        return "Lancer une mise à jour pour récupérer les prochains matchs."
    if health["fixture_details"] < health["upcoming"]:
        return "Ouvrir Matchs à venir pour compléter journées, logos et stades."
    if health["fixture_predictions"] < health["upcoming"]:
        return "Ouvrir une journée dans Matchs à venir pour synchroniser les conseils API."
    return "La base est prête pour consulter les matchs à venir."


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
    if not kpis["completed_count"]:
        return "Aucun match terminé n’est encore disponible pour calculer des tendances fiables."
    dominant = max(
        [
            ("domicile", kpis["home_win_rate"]),
            ("nul", kpis["draw_rate"]),
            ("extérieur", kpis["away_win_rate"]),
        ],
        key=lambda item: item[1],
    )
    return (
        f"Tendance principale: avantage {dominant[0]} ({dominant[1]} %). "
        f"Les deux équipes marquent dans {kpis['btts_rate']} % des matchs et le over 2,5 sort à {kpis['over_25_rate']} %. "
        f"Moyenne globale: {kpis['avg_goals']} buts par match terminé."
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
    health = _load_data_health()

    matches_count = len(matches_df)
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
        "Vue rapide de la base, des matchs disponibles et des tendances utiles pour cadrer les pronostics.",
        [
            ("Matchs importés", _format_int(matches_count)),
            ("Matchs à venir", _format_int(health["upcoming"])),
            ("Conseils API", _format_int(health["fixture_predictions"])),
            ("Saisons sportives", str(seasons)),
        ],
    )

    ui.dashboard_band(
        _quick_read_sentence(kpis),
        [
            ("Saisons sportives", season_scope),
            ("Championnats suivis", league_scope),
            ("Prochaine action", _next_action(health, matches_df)),
            ("Accès API", import_service.get_api_access_message()),
        ],
    )

    ui.section_label("Etat de la base")
    ui.kpi_grid(
        [
            {
                "label": "Matchs terminés",
                "value": _format_int(kpis["completed_count"]),
                "caption": "Matchs avec score complet",
                "accent": "#126447",
            },
            {
                "label": "Détails matchs",
                "value": _format_int(health["fixture_details"]),
                "caption": "Journées, stades et logos en cache",
                "accent": "#d8a528",
            },
            {
                "label": "Résumés prêts",
                "value": _format_int(health["preview_cache"]),
                "caption": "Cartes déjà calculées en SQLite",
                "accent": "#4d7c8a",
            },
        ]
    )

    ui.section_label("Signaux pronostic")
    ui.kpi_grid(
        [
            {
                "label": "Les deux marquent",
                "value": _format_percent(kpis["btts_rate"]),
                "caption": "Signal utile BTTS",
                "accent": "#7a5c96",
            },
            {
                "label": "Plus de 2,5 buts",
                "value": _format_percent(kpis["over_25_rate"]),
                "caption": "Matchs à 3 buts ou plus",
                "accent": "#c94b3f",
            },
            {
                "label": "Matchs nuls",
                "value": _format_percent(kpis["draw_rate"]),
                "caption": "Référence pour double chance",
                "accent": "#8a6f3e",
            },
        ]
    )

    table_cols = st.columns([1, 1])
    upcoming_table = _upcoming_by_league()
    with table_cols[0].container(border=True):
        st.markdown("### Prochains matchs")
        st.caption("Championnat avec le plus de matchs à venir dans la base.")
        if upcoming_table.empty:
            st.info("Aucun match à venir enregistré.")
        else:
            st.dataframe(upcoming_table, width="stretch", hide_index=True)

    readiness_table = _league_readiness_table(matches_df, league_seasons_df)
    with table_cols[1].container(border=True):
        st.markdown("### Championnats suivis")
        st.caption("Couverture des ligues les plus alimentées.")
        if readiness_table.empty:
            st.info("Aucun championnat alimenté.")
        else:
            st.dataframe(readiness_table, width="stretch", hide_index=True)

    st.caption("Les indicateurs sont calculés à partir des matchs présents dans SQLite. Si les données sont vides, allez dans 'Mise à jour'.")


if __name__ == "__main__":
    ui.run_direct_page("Prono insight", show)
