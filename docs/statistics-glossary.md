# Glossaire des statistiques de prédiction

Ce document décrit les indicateurs visibles lors de l’analyse d’un match à
venir. Les légendes de l’interface utilisent les mêmes définitions depuis
`components/statistics_guide.py`.

## Règles générales

- Toutes les statistiques historiques sont calculées avec des matchs dont la
  date est strictement antérieure au coup d’envoi étudié.
- `1`, `N` et `2` signifient victoire à domicile, match nul et victoire à
  l’extérieur. Leur distribution unique totalise 100 %.
- Une probabilité, un indice de solidité, une qualité de données et un consensus
  sont quatre notions distinctes.
- Une donnée absente est signalée ; elle n’est jamais inventée.

## Résultats et forme

| Indicateur | Définition et calcul | Limite principale |
|---|---|---|
| Matchs | Nombre de rencontres terminées disponibles avant le match. | Un grand volume ancien n’est pas toujours plus pertinent qu’une période récente. |
| Taux de victoire | Victoires ÷ matchs analysés × 100. | Décrit le passé, pas la chance directe de gagner le prochain match. |
| Buts par match | Buts marqués ÷ matchs analysés. | Mélange qualité des occasions et efficacité de finition. |
| Encaissés par match | Buts concédés ÷ matchs analysés. | Dépend aussi du niveau des adversaires et des gardiens. |
| Forme | Cinq résultats récents : V, N ou D. | La difficulté des adversaires peut varier. |

## Expected Goals

| Indicateur | Définition et calcul | Lecture |
|---|---|---|
| xG | Somme des probabilités de but attribuées aux tirs par API-Football. | Mesure la qualité cumulée des occasions ; un total peut dépasser 1. |
| xGA | xG créés par l’adversaire, donc occasions concédées. | Une valeur faible est préférable. |
| xG par match | Somme des xG ÷ matchs couverts sur les huit plus récents. | Mesure la création moyenne d’occasions. |
| xGA par match | Somme des xGA ÷ matchs couverts sur la même fenêtre. | Mesure les occasions dangereuses concédées. |
| Différentiel xG | xG par match − xGA par match. | Positif : davantage de qualité créée que concédée. |
| Couverture xG | Matchs possédant les deux valeurs xG ÷ matchs de la fenêtre. | Une faible couverture réduit la représentativité. |

Les xG sont des valeurs **observées après les matchs**. Ils sont distincts des
**buts projetés**, produits avant le prochain match par le modèle interne.

## Probabilités et score

| Indicateur | Définition et calcul | Limite principale |
|---|---|---|
| Probabilités 1/N/2 | Somme des cellules victoire, nul et défaite de la matrice de scores. | Estimations, pas certitudes. |
| Scénario principal | Issue 1/N/2 la plus probable. | Peut rester fragile si la marge est faible. |
| Marge | Écart en points entre les deux premières issues. | Une petite marge indique une affiche indécise. |
| Buts projetés | Moyennes de buts utilisées comme paramètres Poisson. | Ce ne sont pas des xG API observés. |
| Score probable | Cellule la plus probable de la matrice de scores. | Sa probabilité individuelle peut être faible. |
| Couverture des deux | Somme des probabilités du scénario principal et du repli. | Ne doit pas être confondue avec le consensus. |

## Qualité et décision

| Indicateur | Définition | Nature |
|---|---|---|
| Probabilité principale | Maximum de 1/N/2. | Probabilité. |
| Solidité | Marge, qualité, stabilité et accord, sur 100. | Indice, pas probabilité. |
| Qualité des données | Complétude, fraîcheur, historique, compositions et API. | Indice de couverture. |
| Consensus | Accord entre modèle et signaux disponibles. | Indice d’accord. |
| Risque | Incertitude issue des marges, divergences et données manquantes. | Niveau qualitatif. |
| Marché recommandé | Recommandation dérivée uniquement de 1/N/2. | Peut être « Prudence / pas de pari ». |

## Joueurs et tactique

- **Forme du onze** : agrégation normalisée des notes et productions récentes
  des joueurs prévus.
- **Confiance de composition** : qualité de la projection du onze ; elle ne
  mesure pas la chance de victoire.
- **Notes par ligne** : moyennes des joueurs classés en défense, milieu et
  attaque.
- **Avantage tactique** : lecture bornée des oppositions entre dispositifs et
  lignes.
- **Fiabilité tactique** : couverture des compositions, postes, notes et
  formations. Une faible valeur limite l’ajustement.

## Provenance

Les xG affichés viennent d’API-Football, endpoint `/fixtures/statistics`. Chaque
tentative est conservée dans `xg_ingestion_audit` avec l’heure, le statut, la
réponse brute et son empreinte SHA-256. La page « Mise à jour » permet de
consulter ce journal.
