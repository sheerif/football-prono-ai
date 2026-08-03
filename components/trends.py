import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from components import ui
from services.season_format import season_period


STATUS_COLORS = {
    "progression": "#18a66f",
    "regression": "#d55249",
    "stable": "#2aa198",
    "indisponible": "#718096",
}


def _trend_figure(trend: dict, title: str):
    history = trend.get("history") or []
    if len(history) < 2:
        return None
    frame = pd.DataFrame(history)
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["match"] = [
        f"M{index + 1}" for index in range(len(frame))
    ]
    frame["detail"] = [
        f"{row.opponent} · {row.label} · {season_period(row.season)}"
        for row in frame.itertuples()
    ]
    color = STATUS_COLORS.get(trend.get("status"), "#164d73")
    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=frame["match"],
            y=frame["score"],
            mode="lines+markers",
            line={"color": color, "width": 4, "shape": "spline"},
            marker={
                "size": 11,
                "color": frame["score"],
                # Palette sans jaune : rouge = faible, bleu = moyen, turquoise = fort.
                "colorscale": [[0, "#e65b55"], [0.5, "#4f86aa"], [1, "#18a66f"]],
                "cmin": 0,
                "cmax": 100,
                "line": {"color": "#ffffff", "width": 2},
            },
            customdata=frame[["detail", "date"]].values,
            hovertemplate=(
                "<b>%{customdata[0]}</b><br>Indice : %{y:.1f}/100"
                "<br>%{customdata[1]|%d/%m/%Y}<extra></extra>"
            ),
            fill="tozeroy",
            fillcolor="rgba(22,77,115,0.09)",
        )
    )
    figure.add_hline(
        y=50,
        line_dash="dot",
        line_color="rgba(100,116,139,0.45)",
        annotation_text="Repère 50",
    )
    figure.update_layout(
        title={"text": title, "x": 0.02, "font": {"size": 17, "color": "#0b2035"}},
        height=360,
        margin={"l": 30, "r": 20, "t": 55, "b": 35},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"family": "Inter, system-ui, sans-serif", "color": "#263b50"},
        xaxis={"title": "Du plus ancien au plus récent", "showgrid": False},
        yaxis={
            "title": "Indice de performance",
            "range": [0, 105],
            "gridcolor": "rgba(10,34,57,0.09)",
        },
        showlegend=False,
    )
    return figure


def render_trend(trend: dict, title: str, key: str):
    history = trend.get("history") or []
    if len(history) < 2:
        st.info(f"{title} : au moins deux matchs détaillés sont nécessaires.")
        return

    status = trend.get("status", "indisponible")
    delta = float(trend.get("delta") or 0)
    icon = {
        "progression": "📈",
        "regression": "📉",
        "stable": "➡️",
        "indisponible": "⏳",
    }.get(status, "📊")
    st.markdown(f"### {icon} {title}")
    ui.kpi_grid(
        [
            {
                "label": "Tendance",
                "value": trend.get("label", "Indisponible"),
                "caption": f"Écart récent : {delta:+.1f} points",
                "icon": icon,
                "accent": STATUS_COLORS.get(status),
            },
            {
                "label": "Niveau récent",
                "value": f"{trend.get('recent_average', 0):.1f} / 100",
                "caption": "Moyenne de la moitié la plus récente",
                "icon": "🔥",
            },
            {
                "label": "Fiabilité",
                "value": f"{trend.get('confidence', 0)} %",
                "caption": f"{trend.get('match_count', len(history))} match(s) disponible(s)",
                "icon": "✅",
            },
        ]
    )
    if trend.get("season_transition"):
        seasons = " → ".join(season_period(season) for season in trend.get("seasons", []))
        st.info(f"La série traverse plusieurs saisons : {seasons}.")

    figure = _trend_figure(trend, "Évolution sur les 10 derniers matchs")
    if figure is not None:
        st.plotly_chart(
            figure,
            width="stretch",
            config={"displayModeBar": False},
            key=key,
        )
        recent = trend.get("recent_average")
        previous = trend.get("previous_average")
        if recent is not None and previous is not None:
            st.caption(
                f"Résumé : indice moyen {float(recent):.1f}/100 sur les matchs récents "
                f"contre {float(previous):.1f}/100 auparavant."
            )
    if status == "progression":
        st.success(
            "Les performances des matchs les plus récents sont supérieures à "
            "celles du début de la série."
        )
    elif status == "regression":
        st.warning(
            "Les performances récentes reculent par rapport au début de la série."
        )
    else:
        st.info("Aucune progression ou régression nette n’est détectée.")


def render_team_comparison(home_trend: dict, away_trend: dict, home_name: str, away_name: str, key: str):
    ui.section_label("Progression et régression — 10 derniers matchs")
    st.caption(
        "Comparaison des cinq matchs les plus récents aux cinq précédents. "
        "La recherche continue automatiquement dans la saison précédente."
    )
    home_column, away_column = st.columns(2)
    with home_column:
        render_trend(home_trend, home_name, f"{key}_home")
    with away_column:
        render_trend(away_trend, away_name, f"{key}_away")
