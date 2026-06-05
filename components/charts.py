import plotly.express as px
import plotly.graph_objects as go

from services.season_format import season_period


CHART_COLORS = ["#126447", "#d8a528", "#c94b3f", "#4d7c8a", "#7a5c96", "#8a6f3e"]


def _apply_chart_theme(fig, title: str | None = None):
    fig.update_layout(
        title={
            "text": title,
            "font": {"size": 18, "color": "#16201b"},
            "x": 0.02,
            "xanchor": "left",
        } if title else None,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"family": "Inter, system-ui, sans-serif", "color": "#26352d"},
        colorway=CHART_COLORS,
        margin={"l": 20, "r": 20, "t": 56 if title else 24, "b": 28},
        legend={
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.02,
            "xanchor": "right",
            "x": 1,
        },
    )
    fig.update_xaxes(showgrid=False, zeroline=False, title_font={"color": "#66736b"})
    fig.update_yaxes(gridcolor="rgba(22,32,27,0.08)", zeroline=False, title_font={"color": "#66736b"})
    return fig


def pie_matches_distribution(df, col):
    if df.empty or col not in df.columns:
        return None
    values = df[col].value_counts().reset_index()
    values.columns = [col, "count"]
    fig = px.pie(
        values,
        names=col,
        values="count",
        hole=0.58,
        color_discrete_sequence=CHART_COLORS,
    )
    fig.update_traces(textposition="inside", textinfo="percent+label", marker={"line": {"color": "#ffffff", "width": 2}})
    return _apply_chart_theme(fig)


def bar_matches_by_season(df):
    if df.empty or "season" not in df.columns:
        return None
    agg = df.groupby("season", dropna=True).size().reset_index(name="matches")
    agg["season_label"] = agg["season"].apply(season_period)
    fig = px.bar(
        agg,
        x="season_label",
        y="matches",
        title="Nombre de matchs par saison sportive",
        labels={"season_label": "Saison sportive", "matches": "Matchs"},
        color_discrete_sequence=["#126447"],
    )
    fig.update_traces(marker_line_width=0, opacity=0.92)
    return _apply_chart_theme(fig, "Nombre de matchs par saison sportive")


def line_goals_by_season(df):
    if df.empty or "season" not in df.columns:
        return None
    agg = df.groupby("season", dropna=True)[["home_goals", "away_goals"]].sum(numeric_only=True).reset_index()
    agg["total_goals"] = agg["home_goals"].fillna(0) + agg["away_goals"].fillna(0)
    agg["season_label"] = agg["season"].apply(season_period)
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=agg["season_label"],
            y=agg["total_goals"],
            mode="lines+markers",
            name="Buts totaux",
            line={"color": "#d8a528", "width": 3},
            marker={"size": 8, "color": "#126447", "line": {"color": "#ffffff", "width": 2}},
        )
    )
    fig.update_layout(xaxis_title="Saison sportive", yaxis_title="Buts")
    return _apply_chart_theme(fig, "Evolution des buts par saison sportive")


def pie_results(results_df):
    if results_df.empty:
        return None
    fig = px.pie(
        results_df,
        names="result",
        values="count",
        hole=0.58,
        title="Repartition des resultats",
        labels={"result": "Resultat", "count": "Nombre"},
        color_discrete_sequence=CHART_COLORS,
    )
    fig.update_traces(textposition="inside", textinfo="percent+label", marker={"line": {"color": "#ffffff", "width": 2}})
    return _apply_chart_theme(fig, "Repartition des resultats")


def radar_team_comparison(labels, values_a, values_b, name_a="Equipe A", name_b="Equipe B"):
    if not labels:
        return None
    fig = go.Figure()
    fig.add_trace(
        go.Scatterpolar(
            r=values_a,
            theta=labels,
            fill="toself",
            name=name_a,
            line={"color": "#126447", "width": 2},
            fillcolor="rgba(18, 100, 71, 0.20)",
        )
    )
    fig.add_trace(
        go.Scatterpolar(
            r=values_b,
            theta=labels,
            fill="toself",
            name=name_b,
            line={"color": "#c94b3f", "width": 2},
            fillcolor="rgba(201, 75, 63, 0.17)",
        )
    )
    fig.update_layout(
        polar={
            "bgcolor": "rgba(255,255,255,0)",
            "radialaxis": {"visible": True, "gridcolor": "rgba(22,32,27,0.10)"},
            "angularaxis": {"gridcolor": "rgba(22,32,27,0.10)"},
        },
        showlegend=True,
    )
    return _apply_chart_theme(fig, "Comparaison radar")
