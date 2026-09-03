"""Affichage autonome du Ranking Score dans les pages Streamlit."""

from __future__ import annotations

import streamlit as st


def render(prediction: dict) -> None:
    """Affiche les quatre indicateurs sans assimiler le ranking à un pourcentage."""
    columns = st.columns(4)
    columns[0].metric(
        "Probabilité scénario principal",
        f"{prediction.get('confidence', 0)} %",
        help="La plus élevée des probabilités 1/N/2. Ce n’est ni la solidité ni une garantie.",
    )
    columns[0].caption("Probabilité de l’issue classée en tête")
    columns[1].metric(
        "Indice de solidité",
        f"{prediction.get('ranking_score', 0)} / 100",
        help="Indice composite de robustesse : marge, qualité des données, stabilité et accord.",
    )
    columns[1].caption("Indice composite, pas une probabilité")
    columns[2].metric(
        "Qualité des données",
        f"{round(float(prediction.get('data_quality') or 0) * 100)} / 100",
        help="Complétude et fraîcheur de l’historique, des statistiques, des compositions et des sources API.",
    )
    columns[2].caption("Historique, statistiques, fraîcheur et couverture")
    columns[3].metric(
        "Marge",
        f"{prediction.get('margin', 0)} points",
        help="Écart entre les deux issues 1/N/2 classées en tête. Une petite marge indique un match indécis.",
    )
    columns[3].caption("Écart entre les deux premiers scénarios")


def render_decision(prediction: dict) -> None:
    """Affiche consensus, risque et marché sans les assimiler à une probabilité."""
    columns = st.columns(3)
    columns[0].metric(
        "Consensus des sources",
        f"{prediction.get('consensus_score', 0)} / 100",
        help="Mesure l’accord entre le modèle et les signaux disponibles ; ce n’est pas une probabilité.",
    )
    columns[1].metric(
        "Niveau de risque",
        str(prediction.get("risk_level", "modéré")).capitalize(),
        help="Synthèse des marges, divergences et données manquantes.",
    )
    columns[2].metric(
        "Recommandation",
        str(prediction.get("recommended_market", "PRUDENCE")),
        help="Marché dérivé de la distribution 1/N/2. Prudence signifie qu’aucun scénario n’est assez robuste.",
    )
    columns[0].caption("Accord entre le modèle et les signaux disponibles")
    columns[1].caption("Incertitude globale, pas une probabilité")
    columns[2].caption("Marché dérivé uniquement de la distribution 1/N/2")
