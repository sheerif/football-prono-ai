import html

import streamlit as st
import pandas as pd
from database.database import engine
from components import charts, ui
from services import stats_service, prediction_service
from services import prediction_helpers
from services.season_format import season_list, season_period, season_range
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
    return prediction_helpers.fetch_seasons(league_id)


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
                f"SELECT DISTINCT t.id, t.name, t.logo "
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
        return "⏳ À jouer"
    if goals_for > goals_against:
        return "✅ Victoire"
    if goals_for == goals_against:
        return "🟡 Nul"
    return "❌ Défaite"


def _team_matches_history_table(matches_df: pd.DataFrame, team_id: int, team_options: dict[int, str]) -> pd.DataFrame:
    team_matches = matches_df[
        (matches_df["home_team_id"] == team_id) | (matches_df["away_team_id"] == team_id)
    ].copy()
    if team_matches.empty:
        return pd.DataFrame(columns=["Horodatage", "Saison sportive", "Lieu", "Adversaire", "Score", "Résultat", "Statut"])

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
                "Saison sportive": season_period(match.get("season")),
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
        return pd.DataFrame(columns=["Horodatage", "Saison sportive", "Domicile", "Extérieur", "Score", "Vainqueur", "Statut"])

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
                "Saison sportive": season_period(match.get("season")),
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
        return pd.DataFrame(
            columns=[
                "Saison sportive",
                "Horodatage",
                "Domicile",
                "Extérieur",
                "Score",
                f"Résultat {home_name}",
                f"Résultat {away_name}",
            ]
        ), totals

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
        elif away_goals > home_goals:
            winner_id = match["away_team_id"]
        else:
            winner_id = None

        if winner_id is None:
            home_result = "🟡 Nul"
            away_result = "🟡 Nul"
            totals[home_team]["draws"] += 1
            totals[away_team]["draws"] += 1
        elif winner_id == home_team:
            home_result = "✅ Victoire"
            away_result = "❌ Défaite"
            totals[home_team]["wins"] += 1
            totals[away_team]["losses"] += 1
        else:
            home_result = "❌ Défaite"
            away_result = "✅ Victoire"
            totals[away_team]["wins"] += 1
            totals[home_team]["losses"] += 1

        rows.append(
            {
                "Saison sportive": season_period(match.get("season")),
                "Horodatage": _format_timestamp(match.get("date")),
                "Domicile": home_name if match["home_team_id"] == home_team else away_name,
                "Extérieur": away_name if match["away_team_id"] == away_team else home_name,
                "Score": f"{home_goals}-{away_goals}",
                f"Résultat {home_name}": home_result,
                f"Résultat {away_name}": away_result,
            }
        )

    return pd.DataFrame(rows), totals


def _style_result_codes(table: pd.DataFrame):
    def result_style(value):
        text_value = str(value)
        if text_value.startswith("✅") or text_value == "Victoire":
            return (
                "background-color: rgba(34, 197, 94, 0.16); "
                "color: #166534; font-weight: 750;"
            )
        if text_value.startswith("❌") or text_value == "Défaite":
            return (
                "background-color: rgba(239, 68, 68, 0.14); "
                "color: #991b1b; font-weight: 750;"
            )
        if text_value.startswith("🟡") or text_value in {"Nul", "Match nul"}:
            return (
                "background-color: rgba(234, 179, 8, 0.18); "
                "color: #854d0e; font-weight: 750;"
            )
        return ""

    result_columns = [
        column
        for column in table.columns
        if column == "Résultat" or column.startswith("Résultat ")
    ]
    if not result_columns:
        return table
    return table.style.map(result_style, subset=result_columns)


def _recent_matches_table(
    matches_df: pd.DataFrame,
    team_id: int,
    team_options: dict[int, str],
    limit: int = 5,
) -> pd.DataFrame:
    team_matches = matches_df[
        (matches_df["home_team_id"] == team_id)
        | (matches_df["away_team_id"] == team_id)
    ].copy()
    team_matches = (
        team_matches.dropna(subset=["date"])
        .sort_values("date", ascending=False)
        .head(limit)
    )
    rows = []
    result_icons = {"Victoire": "✅ V", "Match nul": "🟡 N", "Défaite": "❌ D"}
    for _, match in team_matches.iterrows():
        home_id = int(match["home_team_id"])
        away_id = int(match["away_team_id"])
        is_home = home_id == int(team_id)
        goals_for = match.get("home_goals") if is_home else match.get("away_goals")
        goals_against = match.get("away_goals") if is_home else match.get("home_goals")
        if pd.isna(goals_for) or pd.isna(goals_against):
            result = "À jouer"
        elif goals_for > goals_against:
            result = "Victoire"
        elif goals_for == goals_against:
            result = "Match nul"
        else:
            result = "Défaite"
        match_date = pd.to_datetime(match.get("date"), errors="coerce")
        rows.append(
            {
                "Date": match_date.strftime("%d/%m") if pd.notna(match_date) else "—",
                "Domicile": team_options.get(home_id, str(home_id)),
                "Score": _score_label(match.get("home_goals"), match.get("away_goals"))
                .replace("Score non disponible", "—"),
                "Extérieur": team_options.get(away_id, str(away_id)),
                "Résultat": result_icons.get(result, "⏳"),
            }
        )
    return pd.DataFrame(rows)


def _form_record(results: list[str]) -> str:
    return (
        f"{results.count('W')} V  ·  {results.count('D')} N  ·  "
        f"{results.count('L')} D"
    )


def _form_badges(results: list[str]) -> str:
    return (
        '<div class="form-badges">'
        f'<span class="form-win">✅ {results.count("W")} V</span>'
        f'<span class="form-draw">🟡 {results.count("D")} N</span>'
        f'<span class="form-loss">❌ {results.count("L")} D</span>'
        "</div>"
    )


def _result_badge_markup(value) -> str:
    result = str(value)
    if result.startswith("✅"):
        css_class, short_label = "result-win", "V"
    elif result.startswith("❌"):
        css_class, short_label = "result-loss", "D"
    elif result.startswith("🟡"):
        css_class, short_label = "result-draw", "N"
    else:
        css_class, short_label = "result-pending", "—"
    return (
        f'<span class="result-code {css_class}" '
        f'title="{html.escape(result, quote=True)}">{short_label}</span>'
    )


def _render_recent_matches_list(table: pd.DataFrame):
    rows = []
    for _, match in table.iterrows():
        rows.append(
            f"""
            <div class="compact-match-row">
                <time>{html.escape(str(match["Date"]))}</time>
                <div class="compact-match-teams">
                    <span>{html.escape(str(match["Domicile"]))}</span>
                    <strong>{html.escape(str(match["Score"]))}</strong>
                    <span>{html.escape(str(match["Extérieur"]))}</span>
                </div>
                {_result_badge_markup(match["Résultat"])}
            </div>
            """
        )
    st.markdown(
        f'<div class="compact-match-list">{"".join(rows)}</div>',
        unsafe_allow_html=True,
    )


def _render_team_history_list(table: pd.DataFrame):
    rows = []
    for _, match in table.iterrows():
        rows.append(
            f"""
            <div class="history-match-row">
                <div class="history-match-main">
                    <strong>{html.escape(str(match["Adversaire"]))}</strong>
                    <span>{html.escape(str(match["Lieu"]))} · {html.escape(str(match["Horodatage"]))}</span>
                </div>
                <strong class="history-score">{html.escape(str(match["Score"]))}</strong>
                {_result_badge_markup(match["Résultat"])}
            </div>
            """
        )
    st.markdown(
        f'<div class="compact-match-list">{"".join(rows)}</div>',
        unsafe_allow_html=True,
    )


def _render_h2h_list(table: pd.DataFrame):
    result_columns = [
        column for column in table.columns if column.startswith("Résultat ")
    ]
    rows = []
    for _, match in table.iterrows():
        result_markup = "".join(
            (
                '<div class="h2h-result">'
                f'<small>{html.escape(column.removeprefix("Résultat "))}</small>'
                f'{_result_badge_markup(match[column])}'
                "</div>"
            )
            for column in result_columns
        )
        rows.append(
            f"""
            <div class="h2h-match-row">
                <time>{html.escape(str(match["Horodatage"]))}</time>
                <div class="compact-match-teams">
                    <span>{html.escape(str(match["Domicile"]))}</span>
                    <strong>{html.escape(str(match["Score"]))}</strong>
                    <span>{html.escape(str(match["Extérieur"]))}</span>
                </div>
                <div class="h2h-results">{result_markup}</div>
            </div>
            """
        )
    st.markdown(
        f'<div class="compact-match-list">{"".join(rows)}</div>',
        unsafe_allow_html=True,
    )


def _team_logo_markup(logo: str | None, team_name: str) -> str:
    if logo:
        return (
            f'<img class="match-team-logo" src="{html.escape(str(logo), quote=True)}" '
            f'alt="Logo {html.escape(team_name, quote=True)}">'
        )
    initials = "".join(part[:1] for part in team_name.split()[:2]).upper()
    return f'<div class="match-team-fallback">{html.escape(initials)}</div>'


def _render_match_header(
    league_name: str,
    season_label: str,
    home_name: str,
    away_name: str,
    home_logo: str | None,
    away_logo: str | None,
    score_prediction: dict,
):
    likely_score = (
        score_prediction["scores"][0]["Score"]
        if score_prediction.get("scores")
        else "VS"
    )
    st.markdown(
        f"""
        <div class="match-sheet">
            <div class="match-sheet-context">
                <span>ANALYSE DU MATCH</span>
                <strong>{html.escape(league_name)}</strong>
                <small>{html.escape(season_label)}</small>
            </div>
            <div class="match-sheet-grid">
                <div class="match-team">
                    {_team_logo_markup(home_logo, home_name)}
                    <strong>{html.escape(home_name)}</strong>
                    <small>Domicile</small>
                </div>
                <div class="match-center">
                    <small>Score le plus probable</small>
                    <strong>{html.escape(likely_score)}</strong>
                    <span>Face-à-face</span>
                </div>
                <div class="match-team">
                    {_team_logo_markup(away_logo, away_name)}
                    <strong>{html.escape(away_name)}</strong>
                    <small>Extérieur</small>
                </div>
            </div>
        </div>
        <style>
        .match-sheet {{
            overflow: hidden;
            margin: 0.3rem 0 1rem;
            border-radius: 14px;
            color: #fff;
            background:
                radial-gradient(circle at 50% 0%, rgba(216,165,40,.22), transparent 34%),
                linear-gradient(135deg, #14251d, #20362b);
            box-shadow: 0 18px 44px rgba(20,37,29,.18);
        }}
        .match-sheet-context {{
            display: flex;
            align-items: center;
            justify-content: center;
            gap: .7rem;
            padding: .75rem 1rem;
            color: rgba(255,255,255,.76);
            border-bottom: 1px solid rgba(255,255,255,.1);
            font-size: .78rem;
        }}
        .match-sheet-context span {{ color: #d8a528; font-weight: 850; }}
        .match-sheet-context strong {{ color: #fff; }}
        .match-sheet-grid {{
            display: grid;
            grid-template-columns: 1fr minmax(120px,.72fr) 1fr;
            align-items: center;
            gap: 1rem;
            padding: 1.5rem;
        }}
        .match-team, .match-center {{
            display: flex;
            flex-direction: column;
            align-items: center;
            text-align: center;
        }}
        .match-team-logo, .match-team-fallback {{
            width: 72px;
            height: 72px;
            margin-bottom: .65rem;
            object-fit: contain;
        }}
        .match-team-fallback {{
            display: grid;
            place-items: center;
            border-radius: 50%;
            background: rgba(255,255,255,.12);
            color: #d8a528;
            font-size: 1.25rem;
            font-weight: 900;
        }}
        .match-team strong {{ font-size: 1.22rem; }}
        .match-team small, .match-center small {{
            margin-top: .25rem;
            color: rgba(255,255,255,.62);
        }}
        .match-center strong {{
            margin: .25rem 0;
            color: #fff;
            font-size: 2.1rem;
            line-height: 1;
        }}
        .match-center span {{
            color: #d8a528;
            font-size: .78rem;
            font-weight: 800;
            text-transform: uppercase;
        }}
        div[data-testid="stTabs"] button[role="tab"] {{
            min-height: 3rem;
            font-weight: 750;
        }}
        .form-badges {{
            display: flex;
            flex-wrap: wrap;
            gap: .45rem;
            margin: .45rem 0 .25rem;
        }}
        .form-badges span {{
            display: inline-flex;
            align-items: center;
            padding: .28rem .52rem;
            border-radius: 7px;
            font-size: .82rem;
            font-weight: 800;
        }}
        .form-win {{
            color: #166534;
            background: rgba(34, 197, 94, .16);
        }}
        .form-draw {{
            color: #854d0e;
            background: rgba(234, 179, 8, .18);
        }}
        .form-loss {{
            color: #991b1b;
            background: rgba(239, 68, 68, .14);
        }}
        .compact-match-list {{
            overflow: hidden;
            border: 1px solid rgba(22, 32, 27, .10);
            border-radius: 10px;
            background: rgba(255, 255, 255, .68);
        }}
        .compact-match-row, .h2h-match-row {{
            display: grid;
            grid-template-columns: 48px minmax(0, 1fr) 34px;
            align-items: center;
            gap: .55rem;
            min-height: 3.25rem;
            padding: .5rem .65rem;
            border-bottom: 1px solid rgba(22, 32, 27, .08);
        }}
        .h2h-match-row {{
            grid-template-columns: 120px minmax(0, 1fr) auto;
        }}
        .compact-match-row:last-child,
        .h2h-match-row:last-child,
        .history-match-row:last-child {{
            border-bottom: 0;
        }}
        .compact-match-row time, .h2h-match-row time {{
            color: #66736b;
            font-size: .76rem;
        }}
        .compact-match-teams {{
            display: grid;
            grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
            align-items: center;
            gap: .45rem;
            min-width: 0;
        }}
        .compact-match-teams span {{
            min-width: 0;
            overflow-wrap: anywhere;
            font-size: .86rem;
            font-weight: 750;
        }}
        .compact-match-teams span:first-child {{ text-align: right; }}
        .compact-match-teams strong {{
            padding: .16rem .35rem;
            border-radius: 5px;
            background: rgba(22, 32, 27, .07);
            white-space: nowrap;
            font-size: .9rem;
            font-weight: 900;
        }}
        .result-code {{
            display: grid;
            place-items: center;
            width: 28px;
            height: 28px;
            border-radius: 7px;
            font-size: .78rem;
            font-weight: 900 !important;
        }}
        .result-win {{ color: #166534; background: rgba(34, 197, 94, .18); }}
        .result-draw {{ color: #854d0e; background: rgba(234, 179, 8, .22); }}
        .result-loss {{ color: #991b1b; background: rgba(239, 68, 68, .17); }}
        .result-pending {{ color: #475569; background: rgba(100, 116, 139, .14); }}
        .history-match-row {{
            display: grid;
            grid-template-columns: minmax(0, 1fr) auto 34px;
            align-items: center;
            gap: .65rem;
            padding: .55rem .7rem;
            border-bottom: 1px solid rgba(22, 32, 27, .08);
        }}
        .history-match-main {{
            display: flex;
            flex-direction: column;
            min-width: 0;
        }}
        .history-match-main strong {{ overflow-wrap: anywhere; }}
        .history-match-main span {{
            color: #66736b;
            font-size: .73rem;
        }}
        .history-score {{
            white-space: nowrap;
            font-weight: 900;
        }}
        .h2h-results {{
            display: flex;
            gap: .4rem;
        }}
        .h2h-result {{
            display: flex;
            align-items: center;
            gap: .25rem;
        }}
        .h2h-result small {{
            max-width: 76px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            color: #66736b;
            font-size: .68rem;
        }}
        @media (max-width: 600px) {{
            .match-sheet-context {{ flex-wrap: wrap; gap: .3rem .55rem; }}
            .match-sheet-grid {{
                grid-template-columns: 1fr 78px 1fr;
                gap: .3rem;
                padding: 1rem .55rem;
            }}
            .match-team-logo, .match-team-fallback {{
                width: 52px;
                height: 52px;
            }}
            .match-team strong {{ font-size: .95rem; }}
            .match-center strong {{ font-size: 1.45rem; }}
            .compact-match-row {{
                grid-template-columns: 38px minmax(0, 1fr) 30px;
                gap: .35rem;
                padding: .48rem .42rem;
            }}
            .compact-match-teams {{ gap: .28rem; }}
            .compact-match-teams span {{ font-size: .76rem; }}
            .compact-match-teams strong {{ font-size: .8rem; }}
            .h2h-match-row {{
                grid-template-columns: 1fr;
                gap: .4rem;
                padding: .65rem;
            }}
            .h2h-match-row time {{ text-align: center; }}
            .h2h-results {{ justify-content: center; }}
            .h2h-result small {{ max-width: 100px; }}
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _legacy_show():
    ui.page_hero(
        "Analyse & comparaison",
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
        selected_seasons = st.multiselect(
            "Saisons sportives",
            options=season_options,
            default=default_window,
            format_func=season_period,
            key="analyse_seasons",
        )
    if not selected_seasons:
        st.warning("Veuillez sélectionner une ou plusieurs saisons.")
        return
    else:
        seasons_window, missing_seasons = prediction_helpers.selected_season_status(selected_seasons, seasons)
        seasons_window = sorted(seasons_window, reverse=True)
        if missing_seasons:
            st.warning(prediction_helpers.missing_seasons_message(missing_seasons, seasons_window))
        if not seasons_window:
            st.warning("Aucune saison sélectionnée n'est disponible dans la base pour ce championnat.")
            return

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

    if st.button("Analyser le match", type="primary", width="stretch"):
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
        st.caption(f"{league_map[league_id]} · Analyse sur {season_range(seasons_window)} (saisons sportives sélectionnées)")
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

        st.subheader("Comparaison radar")
        home_form_score = (
            sum(3 if result == "W" else 1 if result == "D" else 0 for result in home_form)
            / max(1, 3 * len(home_form))
            if home_form
            else 0
        )
        away_form_score = (
            sum(3 if result == "W" else 1 if result == "D" else 0 for result in away_form)
            / max(1, 3 * len(away_form))
            if away_form
            else 0
        )
        radar = charts.radar_team_comparison(
            ["Victoires", "Buts marqués", "Solidité défensive", "Forme", "Expérience"],
            [
                home_stats["wins"],
                home_stats["goals_for"] / max(1, home_stats["played"]),
                max(0, 10 - home_stats["goals_against"] / max(1, home_stats["played"])),
                home_form_score * 10,
                home_stats["played"],
            ],
            [
                away_stats["wins"],
                away_stats["goals_for"] / max(1, away_stats["played"]),
                max(0, 10 - away_stats["goals_against"] / max(1, away_stats["played"])),
                away_form_score * 10,
                away_stats["played"],
            ],
            home_view["team_name"],
            away_view["team_name"],
        )
        if radar is not None:
            st.plotly_chart(radar, width="stretch", key="radar_match_analysis")

        st.subheader("Forme récente")
        home_recent_10 = home_form
        away_recent_10 = away_form
        form_cols = st.columns(2)
        form_cols[0].write(f"**{home_view['team_name']}**")
        form_cols[0].write(home_view['form_text'])
        form_cols[1].write(f"**{away_view['team_name']}**")
        form_cols[1].write(away_view['form_text'])

        st.subheader("Matchs existants dans les saisons sportives sélectionnées")
        st.caption(
            "Ces tableaux affichent tous les matchs présents dans la base pour les saisons sportives sélectionnées, avec leur horodatage. "
            "Les matchs sans score sont visibles mais ne sont pas comptés dans les statistiques de forme."
        )
        home_history, away_history, h2h_history = st.tabs(
            [home_view['team_name'], away_view['team_name'], "Confrontations directes"]
        )
        with home_history:
            home_history_table = _team_matches_history_table(matches_df, home_team, team_options)
            if home_history_table.empty:
                st.info(f"Aucun match trouvé pour {home_view['team_name']} dans les saisons sportives sélectionnées.")
            else:
                st.dataframe(home_history_table, width="stretch", hide_index=True)
        with away_history:
            away_history_table = _team_matches_history_table(matches_df, away_team, team_options)
            if away_history_table.empty:
                st.info(f"Aucun match trouvé pour {away_view['team_name']} dans les saisons sportives sélectionnées.")
            else:
                st.dataframe(away_history_table, width="stretch", hide_index=True)
        with h2h_history:
            h2h_history_df = _h2h_history_table(matches_df, home_team, away_team, team_options)
            if h2h_history_df.empty:
                st.info("Aucune confrontation directe trouvée dans les saisons sportives sélectionnées.")
            else:
                st.dataframe(h2h_history_df, width="stretch", hide_index=True)

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
            st.dataframe(h2h_table, width="stretch", hide_index=True)
        else:
            st.info("Aucune confrontation trouvée sur la fenêtre choisie.")

        completed_matches_df = matches_df.dropna(subset=["home_goals", "away_goals"])
        total_matches = len(completed_matches_df)
        total_goals = int(
            completed_matches_df["home_goals"].sum()
            + completed_matches_df["away_goals"].sum()
        )
        average_goals = round(total_goals / total_matches, 2) if total_matches else 0
        per_season = (
            completed_matches_df.groupby("season")
            .agg(
                matches=("fixture_id", "size"),
                home_goals=("home_goals", "sum"),
                away_goals=("away_goals", "sum"),
            )
            .reset_index()
            .sort_values("season", ascending=False)
        )
        season_rows = [
            {
                "season": int(row["season"]),
                "matches": int(row["matches"]),
                "avg_goals": round(
                    (row["home_goals"] + row["away_goals"]) / max(1, row["matches"]),
                    2,
                ),
            }
            for _, row in per_season.iterrows()
        ]
        with st.expander("Contexte du championnat"):
            ui.season_summary(
                "Bilan du championnat",
                (
                    f"{league_map[league_id]} — saisons sportives : "
                    f"{season_list(seasons_window)}. Matchs terminés uniquement."
                ),
                [
                    ("Matchs terminés", f"{total_matches:,}".replace(",", " ")),
                    ("Buts marqués", f"{total_goals:,}".replace(",", " ")),
                    ("Moyenne buts / match", average_goals),
                ],
                season_rows,
            )

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
        score_prediction = prediction_service.predict_scorelines(
            matches_df,
            home_team,
            away_team,
            home_form_score=home_form,
            away_form_score=away_form,
            top_n=6,
        )
        expected_cols = st.columns(2)
        expected_cols[0].metric(f"Buts attendus {home_view['team_name']}", score_prediction["expected_home_goals"])
        expected_cols[1].metric(f"Buts attendus {away_view['team_name']}", score_prediction["expected_away_goals"])
        if score_prediction["scores"]:
            st.dataframe(pd.DataFrame(score_prediction["scores"]), width="stretch", hide_index=True)
            best_score = score_prediction["scores"][0]
            st.success(f"Score le plus probable: {best_score['Score']} ({best_score['Probabilité']} %)")
            st.caption(score_prediction["method"])
        else:
            st.info(score_prediction["method"])

        st.caption("Le modèle combine la forme récente, le niveau offensif/défensif et l’historique direct pour produire une estimation probabiliste.")

def show():
    ui.page_hero(
        "Analyse & comparaison",
        "Une fiche de match claire pour comparer la forme, les statistiques, les confrontations directes et la prédiction.",
    )

    league_map = _league_options()
    ui.section_label("Choisir l'affiche")
    with st.container(border=True):
        league_id = st.selectbox(
            "Championnat",
            options=list(league_map.keys()),
            format_func=lambda key: league_map[key],
        )

        seasons = _fetch_seasons_for_league(league_id)
        if not seasons:
            st.warning("Aucune saison trouvée pour ce championnat.")
            return

        default_window = [
            season
            for season in sorted(_season_window(seasons[0], 10), reverse=True)
            if season in seasons
        ]
        selected_seasons = st.multiselect(
            "Saisons sportives",
            options=sorted(prediction_helpers.configured_seasons(), reverse=True),
            default=default_window,
            format_func=season_period,
            key="analyse_seasons_sheet",
        )

        seasons_window, missing_seasons = prediction_helpers.selected_season_status(
            selected_seasons, seasons
        )
        seasons_window = sorted(seasons_window, reverse=True)
        if missing_seasons:
            st.warning(
                prediction_helpers.missing_seasons_message(
                    missing_seasons, seasons_window
                )
            )
        if not seasons_window:
            st.warning(
                "Sélectionnez au moins une saison disponible pour ce championnat."
            )
            return

        teams_df = _fetch_teams(league_id, seasons_window)
        if teams_df.empty:
            st.warning("Aucune équipe disponible pour cette sélection.")
            return

        team_options = {int(row.id): row.name for row in teams_df.itertuples()}
        team_logos = {
            int(row.id): getattr(row, "logo", None) for row in teams_df.itertuples()
        }
        team_cols = st.columns(2)
        home_team = team_cols[0].selectbox(
            "Équipe domicile",
            options=list(team_options),
            format_func=lambda key: team_options[key],
        )
        away_team = team_cols[1].selectbox(
            "Équipe extérieure",
            options=[key for key in team_options if key != home_team],
            format_func=lambda key: team_options[key],
        )

        st.caption(
            prediction_helpers.teams_available_message(
                len(team_options), seasons_window
            )
        )
        analyse = st.button(
            "Afficher la fiche du match", type="primary", width="stretch"
        )

    if not analyse:
        st.info(
            "Choisissez les deux équipes puis affichez la fiche pour consulter "
            "l'analyse complète."
        )
        return

    matches_df = _load_matches_window(league_id, seasons_window)
    if matches_df.empty:
        st.warning("Aucun match disponible sur les saisons retenues.")
        return

    home_stats = stats_service.compute_basic_stats(matches_df, home_team)
    away_stats = stats_service.compute_basic_stats(matches_df, away_team)
    home_results = _last_results(matches_df, home_team, 10)
    away_results = _last_results(matches_df, away_team, 10)
    home_view = _team_summary_metrics(
        team_options[home_team], home_stats, home_results
    )
    away_view = _team_summary_metrics(
        team_options[away_team], away_stats, away_results
    )

    def form_score(results):
        if not results:
            return 0.5
        points = sum(
            3 if result == "W" else 1 if result == "D" else 0
            for result in results
        )
        return points / (3 * len(results))

    home_form_score = form_score(home_results)
    away_form_score = form_score(away_results)
    home_attack = home_stats["goals_for"] / max(1, home_stats["played"])
    home_defense = home_stats["goals_against"] / max(1, home_stats["played"])
    away_attack = away_stats["goals_for"] / max(1, away_stats["played"])
    away_defense = away_stats["goals_against"] / max(1, away_stats["played"])
    home_strength = (
        0.6 * home_form_score
        + 0.2 * (home_attack - away_defense)
        + 0.1
    )
    away_strength = (
        0.6 * away_form_score
        + 0.2 * (away_attack - home_defense)
        + 0.1
    )
    prediction = prediction_service.predict_simple(home_strength, away_strength)
    score_prediction = prediction_service.predict_scorelines(
        matches_df,
        home_team,
        away_team,
        home_form_score=home_form_score,
        away_form_score=away_form_score,
        top_n=6,
    )

    _render_match_header(
        league_map[league_id],
        season_range(seasons_window),
        home_view["team_name"],
        away_view["team_name"],
        team_logos.get(home_team),
        team_logos.get(away_team),
        score_prediction,
    )

    overview_tab, form_tab, h2h_tab, stats_tab, prediction_tab = st.tabs(
        [
            "Vue d'ensemble",
            "Forme",
            "Face-à-face",
            "Statistiques",
            "Prédiction",
        ]
    )

    with overview_tab:
        st.subheader("Les deux équipes en un coup d'œil")
        home_column, away_column = st.columns(2)
        for column, view, venue in (
            (home_column, home_view, "Domicile"),
            (away_column, away_view, "Extérieur"),
        ):
            with column:
                with st.container(border=True):
                    st.markdown(f"### {view['team_name']}")
                    st.caption(venue)
                    summary_columns = st.columns(3)
                    summary_columns[0].metric("Matchs", view["played"])
                    summary_columns[1].metric("Victoires", view["wins"])
                    summary_columns[2].metric("Taux", f"{view['win_pct']} %")
                    goal_columns = st.columns(2)
                    goal_columns[0].metric(
                        "Buts / match", view["goals_for_avg"]
                    )
                    goal_columns[1].metric(
                        "Encaissés / match", view["goals_against_avg"]
                    )
                    st.markdown("**Forme récente**")
                    st.markdown(
                        _form_badges(
                            home_results
                            if venue == "Domicile"
                            else away_results
                        ),
                        unsafe_allow_html=True,
                    )

        probability_columns = st.columns(3)
        probability_columns[0].metric(
            f"Victoire {home_view['team_name']}",
            f"{prediction['home_probability']} %",
        )
        probability_columns[1].metric(
            "Match nul", f"{prediction['draw_probability']} %"
        )
        probability_columns[2].metric(
            f"Victoire {away_view['team_name']}",
            f"{prediction['away_probability']} %",
        )

        st.subheader("Profil comparé")
        st.caption(
            "Quatre indicateurs simples, tous ramenés sur 100. Plus la zone "
            "est étendue, meilleur est le profil."
        )
        radar = charts.radar_team_comparison(
            ["Victoires", "Attaque", "Défense", "Forme"],
            [
                home_stats["wins"] / max(1, home_stats["played"]) * 100,
                min(100, home_attack / 3 * 100),
                max(0, 100 - home_defense / 3 * 100),
                home_form_score * 100,
            ],
            [
                away_stats["wins"] / max(1, away_stats["played"]) * 100,
                min(100, away_attack / 3 * 100),
                max(0, 100 - away_defense / 3 * 100),
                away_form_score * 100,
            ],
            home_view["team_name"],
            away_view["team_name"],
        )
        if radar is not None:
            st.plotly_chart(
                radar,
                width="stretch",
                key="simple_radar_match_sheet",
            )

    with form_tab:
        st.subheader("Forme — 5 derniers matchs")
        st.caption(
            "✅ victoire · 🟡 match nul · ❌ défaite. Le résultat est toujours "
            "lu du point de vue de l'équipe présentée."
        )
        home_form_column, away_form_column = st.columns(2)
        for column, team_id, view, results in (
            (home_form_column, home_team, home_view, home_results),
            (away_form_column, away_team, away_view, away_results),
        ):
            with column:
                with st.container(border=True):
                    st.markdown(f"### {view['team_name']}")
                    st.markdown(
                        _form_badges(results[:5]),
                        unsafe_allow_html=True,
                    )
                    recent_table = _recent_matches_table(
                        matches_df, team_id, team_options, 5
                    )
                    if recent_table.empty:
                        st.info("Aucun match récent disponible.")
                    else:
                        _render_recent_matches_list(recent_table)

        with st.expander("Voir l'historique complet des deux équipes"):
            history_home, history_away = st.tabs(
                [home_view["team_name"], away_view["team_name"]]
            )
            with history_home:
                home_history_table = _team_matches_history_table(
                    matches_df, home_team, team_options
                )
                if home_history_table.empty:
                    st.info("Aucun historique disponible.")
                else:
                    _render_team_history_list(home_history_table)
            with history_away:
                away_history_table = _team_matches_history_table(
                    matches_df, away_team, team_options
                )
                if away_history_table.empty:
                    st.info("Aucun historique disponible.")
                else:
                    _render_team_history_list(away_history_table)

    h2h_df = matches_df[
        (
            (matches_df["home_team_id"] == home_team)
            & (matches_df["away_team_id"] == away_team)
        )
        | (
            (matches_df["home_team_id"] == away_team)
            & (matches_df["away_team_id"] == home_team)
        )
    ].copy()
    h2h_table, h2h_totals = _build_h2h_report(
        h2h_df,
        home_team,
        away_team,
        home_view["team_name"],
        away_view["team_name"],
    )

    with h2h_tab:
        st.subheader("Confrontations directes")
        h2h_columns = st.columns(4)
        h2h_columns[0].metric("Matchs", len(h2h_table))
        h2h_columns[1].metric(
            f"Victoires {home_view['team_name']}",
            h2h_totals[home_team]["wins"],
        )
        h2h_columns[2].metric("Nuls", h2h_totals[home_team]["draws"])
        h2h_columns[3].metric(
            f"Victoires {away_view['team_name']}",
            h2h_totals[away_team]["wins"],
        )
        if h2h_table.empty:
            st.info("Aucune confrontation trouvée sur la période choisie.")
        else:
            st.caption(
                "✅ victoire · 🟡 match nul · ❌ défaite, du point de vue de "
                "chaque équipe."
            )
            _render_h2h_list(h2h_table)

    with stats_tab:
        st.subheader("Contexte statistique")
        completed_matches = matches_df.dropna(
            subset=["home_goals", "away_goals"]
        )
        total_matches = len(completed_matches)
        total_goals = int(
            completed_matches["home_goals"].sum()
            + completed_matches["away_goals"].sum()
        )
        per_season = (
            completed_matches.groupby("season")
            .agg(
                matches=("fixture_id", "size"),
                home_goals=("home_goals", "sum"),
                away_goals=("away_goals", "sum"),
            )
            .reset_index()
            .sort_values("season", ascending=False)
        )
        season_rows = [
            {
                "season": int(row["season"]),
                "matches": int(row["matches"]),
                "avg_goals": round(
                    (row["home_goals"] + row["away_goals"])
                    / max(1, row["matches"]),
                    2,
                ),
            }
            for _, row in per_season.iterrows()
        ]
        with st.expander("Contexte du championnat"):
            ui.season_summary(
                "Bilan du championnat",
                (
                    f"{league_map[league_id]} — saisons : "
                    f"{season_list(seasons_window)}."
                ),
                [
                    ("Matchs terminés", f"{total_matches:,}".replace(",", " ")),
                    ("Buts marqués", f"{total_goals:,}".replace(",", " ")),
                    (
                        "Moyenne buts / match",
                        round(total_goals / total_matches, 2)
                        if total_matches
                        else 0,
                    ),
                ],
                season_rows,
            )

    with prediction_tab:
        st.subheader("Prédiction du match")
        prediction_columns = st.columns(4)
        prediction_columns[0].metric(
            f"Victoire {home_view['team_name']}",
            f"{prediction['home_probability']} %",
        )
        prediction_columns[1].metric(
            "Match nul", f"{prediction['draw_probability']} %"
        )
        prediction_columns[2].metric(
            f"Victoire {away_view['team_name']}",
            f"{prediction['away_probability']} %",
        )
        prediction_columns[3].metric(
            "Confiance", f"{prediction.get('confidence')} %"
        )

        reasons = []
        if home_form_score > away_form_score:
            reasons.append(
                f"Meilleure forme récente pour {home_view['team_name']}"
            )
        elif away_form_score > home_form_score:
            reasons.append(
                f"Meilleure forme récente pour {away_view['team_name']}"
            )
        if home_attack > away_attack:
            reasons.append(
                f"Avantage offensif pour {home_view['team_name']}"
            )
        elif away_attack > home_attack:
            reasons.append(
                f"Avantage offensif pour {away_view['team_name']}"
            )
        if home_defense < away_defense:
            reasons.append(
                f"Défense plus solide pour {home_view['team_name']}"
            )
        elif away_defense < home_defense:
            reasons.append(
                f"Défense plus solide pour {away_view['team_name']}"
            )

        st.markdown("#### Pourquoi ce résultat ?")
        for reason in reasons or ["Aucune tendance forte dans les données."]:
            st.write(f"- {reason}")

        st.markdown("#### Scores probables")
        expected_columns = st.columns(2)
        expected_columns[0].metric(
            f"Buts attendus {home_view['team_name']}",
            score_prediction["expected_home_goals"],
        )
        expected_columns[1].metric(
            f"Buts attendus {away_view['team_name']}",
            score_prediction["expected_away_goals"],
        )
        if score_prediction["scores"]:
            st.dataframe(
                pd.DataFrame(score_prediction["scores"]),
                width="stretch",
                hide_index=True,
            )
            best_score = score_prediction["scores"][0]
            st.success(
                f"Score le plus probable : {best_score['Score']} "
                f"({best_score['Probabilité']} %)"
            )
        st.caption(score_prediction["method"])


if __name__ == "__main__":
    ui.run_direct_page("Analyse & comparaison", show)
