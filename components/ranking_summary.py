"""Affichage autonome du Ranking Score dans les pages Streamlit."""

from __future__ import annotations

import streamlit as st


def render(prediction: dict) -> None:
    """Affiche les quatre indicateurs sans assimiler le ranking à un pourcentage."""
    columns = st.columns(4)
    columns[0].metric(
        "Probabilité scénario principal",
        f"{prediction.get('confidence', 0)} %",
    )
    columns[0].caption("Probabilité de l’issue classée en tête")
    columns[1].metric(
        "Indice de solidité",
        f"{prediction.get('ranking_score', 0)} / 100",
    )
    columns[1].caption("Indice composite, pas une probabilité")
    columns[2].metric(
        "Qualité des données",
        f"{round(float(prediction.get('data_quality') or 0) * 100)} / 100",
    )
    columns[2].caption("Historique, statistiques, fraîcheur et couverture")
    columns[3].metric(
        "Marge",
        f"{prediction.get('margin', 0)} points",
    )
    columns[3].caption("Écart entre les deux premiers scénarios")
