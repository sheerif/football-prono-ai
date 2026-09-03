"""Légendes canoniques des statistiques affichées pendant une prédiction."""

from __future__ import annotations

import pandas as pd
import streamlit as st


GLOSSARY = {
    "overview": [
        ("Matchs", "Rencontres terminées utilisées pour décrire l’équipe.", "Comptage de l’historique disponible avant le coup d’envoi.", "Plus l’échantillon est grand, plus le résumé est stable."),
        ("Taux de victoire", "Part des matchs gagnés.", "Victoires ÷ matchs analysés × 100.", "Décrit le passé ; ce n’est pas la probabilité du prochain match."),
        ("Buts / match", "Production offensive réelle moyenne.", "Buts marqués ÷ matchs analysés.", "À comparer aux xG pour distinguer occasions et finition."),
        ("Encaissés / match", "Buts réellement concédés en moyenne.", "Buts encaissés ÷ matchs analysés.", "Une valeur faible indique une meilleure performance défensive passée."),
        ("Probabilités 1/N/2", "Chances estimées de victoire domicile, nul et victoire extérieure.", "Issues d’une distribution unique de scores ; somme = 100 %.", "Ce sont des estimations, jamais des certitudes."),
        ("Probabilité du scénario principal", "Probabilité de l’issue 1/N/2 classée première.", "Maximum des trois probabilités 1/N/2.", "Ne pas confondre avec la solidité ou la qualité des données."),
        ("Indice de solidité", "Robustesse globale de la recommandation.", "Indice 0–100 combinant marge, qualité, stabilité et accord.", "Ce n’est pas une probabilité de réussite."),
        ("Qualité des données", "Niveau de complétude et de fraîcheur des entrées.", "Historique, statistiques, compositions, fraîcheur et couverture API.", "Une valeur faible impose davantage de prudence."),
        ("Marge", "Écart entre les deux issues 1/N/2 les plus probables.", "Première probabilité − deuxième probabilité, en points.", "Une petite marge signale un match indécis."),
        ("Consensus", "Accord entre le modèle et les signaux disponibles.", "Indice distinct des probabilités, exprimé sur 100.", "Un consensus élevé ne supprime pas l’incertitude du football."),
        ("Risque", "Synthèse qualitative de l’incertitude.", "Tient compte de la marge, des divergences et des données manquantes.", "Faible, modéré ou élevé ; ce n’est pas une cote."),
        ("Marché recommandé", "Issue prudente dérivée des probabilités.", "Produit uniquement à partir de la distribution 1/N/2.", "« Prudence » signifie qu’aucun pari n’est suffisamment robuste."),
    ],
    "form": [
        ("Forme récente", "Suite des résultats les plus récents.", "Cinq matchs maximum : V = victoire, N = nul, D = défaite.", "La récence est utile mais l’opposition rencontrée peut varier."),
        ("Score", "Buts réellement marqués par les deux équipes.", "Résultat officiel du match terminé.", "Le score ne mesure pas à lui seul la qualité des occasions."),
        ("xG du match", "Qualité cumulée des occasions créées.", "Somme des probabilités de but attribuées aux tirs par API-Football.", "Un total peut dépasser 1 ; 2,0 xG ne signifie pas exactement 2 buts."),
        ("xGA du match", "Qualité cumulée des occasions concédées.", "xG produits par l’adversaire.", "Une valeur faible indique que peu d’occasions dangereuses ont été concédées."),
    ],
    "lineups": [
        ("Composition officielle/probable", "Onze publié ou estimé pour le match.", "L’officiel vient de l’API ; le probable est fondé sur les données antérieures.", "Une composition probable peut différer du choix réel de l’entraîneur."),
        ("Confiance de composition", "Degré de confiance dans un onze probable.", "Couverture et régularité des titularisations disponibles.", "Ce n’est pas une probabilité de victoire."),
        ("Forme du onze", "Indice synthétique des joueurs prévus.", "Notes et productions récentes, normalisées sur 100.", "Dépend directement de la disponibilité des statistiques joueurs."),
        ("Note moyenne", "Moyenne des évaluations individuelles disponibles.", "Moyenne des notes API des joueurs retenus.", "Comparer uniquement des équipes avec une couverture similaire."),
        ("Notes par ligne", "Évaluation de la défense, du milieu et de l’attaque.", "Agrégation des notes des joueurs par poste.", "Décrit les joueurs prévus, pas automatiquement l’organisation collective."),
        ("Avantage tactique", "Équipe favorisée par l’opposition des dispositifs.", "Écarts attaque/défense, milieu/milieu et profils de formations.", "Ajustement borné ; les consignes réelles restent inconnues."),
        ("Fiabilité tactique", "Qualité des informations utilisées par l’analyse tactique.", "Complétude des compositions, postes, notes et dispositifs.", "Une faible fiabilité réduit fortement l’impact tactique."),
    ],
    "h2h": [
        ("Face-à-face", "Rencontres précédentes entre les deux équipes.", "Comptage des victoires, nuls et défaites avant le match étudié.", "Un ancien duel peut être peu pertinent après changement d’effectif ou d’entraîneur."),
        ("Victoires H2H", "Nombre de confrontations gagnées par chaque équipe.", "Comparaison des scores officiels.", "À interpréter avec le nombre total de confrontations."),
        ("Nuls H2H", "Nombre de confrontations terminées à égalité.", "Égalité des buts au terme du match.", "Un petit échantillon ne constitue pas une tendance solide."),
    ],
    "statistics": [
        ("xG / match", "Qualité moyenne des occasions créées.", "Somme des xG produits ÷ matchs couverts, sur les 8 plus récents.", "Plus élevé = davantage ou de meilleures occasions."),
        ("xGA / match", "Qualité moyenne des occasions concédées.", "Somme des xG adverses ÷ matchs couverts, sur les 8 plus récents.", "Plus faible = moins d’occasions dangereuses concédées."),
        ("Différentiel xG", "Équilibre entre création et concession d’occasions.", "xG par match − xGA par match.", "Positif = l’équipe crée de meilleures occasions qu’elle n’en concède."),
        ("Couverture xG", "Part de la fenêtre possédant deux valeurs xG exploitables.", "Matchs avec xG domicile et extérieur ÷ matchs de la fenêtre.", "Une faible couverture rend la moyenne moins représentative."),
        ("Attaque du radar", "Indice visuel fondé sur les buts marqués.", "Buts par match ramenés sur une échelle 0–100.", "Ce n’est pas un xG ni une probabilité."),
        ("Défense du radar", "Indice visuel inversant les buts encaissés.", "Moins de buts encaissés donne une valeur plus élevée, bornée à 100.", "Sert à comparer les profils, pas à prédire seul le score."),
    ],
    "prediction": [
        ("Buts projetés", "Nombre moyen de buts estimé avant le match pour chaque équipe.", "Paramètres de la matrice de scores Poisson, calculés avec l’historique antérieur.", "Ce ne sont pas les xG observés fournis après un match."),
        ("Score probable", "Score exact ayant la plus forte probabilité individuelle.", "Cellule la plus probable de la matrice de scores.", "Même le score classé premier peut rester peu probable."),
        ("Probabilité du score", "Chance attribuée à un score exact.", "Probabilité de la cellule correspondante dans la matrice.", "Les probabilités de tous les scores possibles se répartissent le total."),
        ("Indice joueurs", "Forme agrégée du onze prévu.", "Valeur normalisée de 0 à 100.", "Utilisé seulement si la couverture joueurs est suffisante."),
        ("Ajustement 1/N/2", "Déplacement appliqué entre domicile et extérieur.", "Effets joueurs et tactiques pondérés par leur fiabilité et plafonnés.", "Un signe positif favorise le domicile ; un signe négatif l’extérieur."),
        ("Scénario principal", "Issue 1/N/2 classée première.", "Issue ayant la probabilité finale la plus élevée.", "Une première place avec faible marge appelle à la prudence."),
        ("Scénario de repli", "Deuxième issue la plus probable.", "Issue classée immédiatement après le scénario principal.", "Permet de représenter l’alternative dominante."),
        ("Couverture des deux", "Probabilité cumulée des deux premières issues.", "Scénario principal + scénario de repli.", "Correspond à une double chance uniquement lorsque les deux issues la définissent."),
        ("Conseil API", "Signal externe séparé du modèle statistique.", "Probabilités et conseil publiés par API-Football lorsque disponibles.", "Une réponse neutre ou indisponible n’est pas utilisée comme preuve."),
    ],
}


def render(section: str, *, expanded: bool = False) -> None:
    """Affiche la légende adaptée à l'onglet courant."""
    entries = GLOSSARY.get(section, [])
    if not entries:
        return
    with st.expander("ℹ️ Comprendre les statistiques et leurs limites", expanded=expanded):
        st.dataframe(
            pd.DataFrame(
                entries,
                columns=["Indicateur", "Définition", "Calcul ou source", "Interprétation"],
            ),
            hide_index=True,
            width="stretch",
        )
        st.caption(
            "Toutes les statistiques historiques sont limitées aux informations "
            "connues avant le coup d’envoi. Une estimation décrit une probabilité, "
            "jamais une garantie de résultat."
        )
