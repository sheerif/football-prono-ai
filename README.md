# Prono insight

Application Streamlit d’aide à l’analyse football avec dashboard, imports
API-Football, widgets API-Sports, statistiques, estimations 1/N/2 expérimentales
et suivi persistant des mises à jour.

Les scores 1/N/2 sont des estimations internes issues de règles statistiques.
Ils ne constituent ni une fiabilité calibrée ni un conseil de pari.

Lorsqu’un conseil API-Football est disponible, il est présenté comme un signal
distinct pour comparaison. Il ne modifie pas la distribution 1/N/2 canonique,
qui provient de la matrice de scores du modèle statistique.

## Quick Start

1. Create virtualenv and install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Copy `.env.example` to `.env` and set your credentials:

```bash
cp .env.example .env
```

Use unique values for `APP_USERNAME` and `APP_PASSWORD`; known defaults such
as `admin/admin` are rejected. Authentication remains in the Streamlit session
and is never stored in the URL.

The live widget requires a dedicated `API_FOOTBALL_WIDGET_KEY`; it never
falls back to the engine's `API_FOOTBALL_KEY`, so browser-side widget requests
cannot bypass the engine's shared quota policy. The key is visible to authenticated
users in the iframe; restrict it by domain and quota. Widget requests and their
separate key are not included in the engine's API metrics.

3. Run the Streamlit app:

```bash
streamlit run app.py
```

The app creates SQLite tables on first run.

### Synchronisation exhaustive et quotas

L’action **Synchronisation exhaustive** parcourt toutes les ligues et saisons
configurées ainsi que tous les matchs connus. Elle conserve dans SQLite les
données utilisées par l’application : championnats, équipes, matchs,
classements, détails de rencontre, compositions, joueurs, performances,
prédictions API et statistiques de match, dont les xG.

Chaque ressource reçue est validée immédiatement et son état est enregistré
dans `resource_sync_state`. Si API-Football refuse une requête à cause du
quota, le traitement mémorise son point de reprise, attend le renouvellement
du quota puis continue en ignorant les ressources déjà complètes. Cet état
survit à un redémarrage tant que le fichier `football.db` est conservé.

La même politique différentielle s'applique aux données principales par
ligue/saison, détails de match, compositions, conseils API, statistiques des
joueurs par match, joueurs par saison et xG. La présence des lignes métier est
contrôlée avant le registre afin de réparer un marqueur devenu incohérent. Les
réponses indisponibles sont temporisées et ne sont plus redemandées pour les
anciens matchs après leur fenêtre de publication. Les saisons récentes ne sont
rafraîchies qu'après `CORE_SYNC_REFRESH_HOURS` (6 heures par défaut). Le journal
des tâches indique le nombre réel d'appels et le minimum de requêtes évitées.
Pendant une tâche, la barre actualisée chaque seconde affiche le pourcentage,
le nombre d'éléments traités sur le total, les téléchargements réussis, les
ressources déjà présentes et les appels API lorsque ces compteurs sont
disponibles pour la phase en cours.
Si un quota interrompt un nouveau passage avant son premier appel, la barre
utilise la couverture réellement persistée dans les tables plutôt qu'un faux
zéro. Les événements identiques produits dans la même minute sont dédupliqués
dans l'historique des mises à jour.

Par défaut, une limite par minute est contrôlée après 90 secondes. Pour le quota
journalier de ce forfait, la reprise est programmée à minuit UTC. L'application
n'attend aucune confirmation de `/status`. À l'échéance, elle relance directement
la synchronisation et laisse la première requête métier confirmer la disponibilité
réelle. Le bouton manuel suit la même règle. La tâche libère l’interface pendant
l’attente. `FULL_SYNC_QUOTA_RETRY_SECONDS` permet de forcer un autre délai de
secours (minimum 60 secondes). Le
mécanisme ne contourne pas les limites du fournisseur : il étale automatiquement
le téléchargement sur plusieurs fenêtres de quota.

Les matchs en cours et à venir passent en premier : l'application actualise
d'abord les saisons récentes, puis les conseils des rencontres à venir, les
détails et les compositions des rencontres concernées. Le rattrapage xG et les
autres données historiques viennent ensuite. `XG_DAILY_RESERVE` conserve par
défaut 1 500 requêtes lorsque le rattrapage xG est en cours ; cette réserve est
configurable. `FULL_SYNC_DAILY_RESERVE` permet aussi de conserver une réserve
en fin de synchronisation (valeur par défaut : `0`).
La limite du fournisseur reste incontournable ; lorsqu'elle est atteinte, la
progression est conservée jusqu'à minuit UTC.
Tous les clients API partagent aussi un coupe-circuit en mémoire : dès qu'une
réponse indique zéro requête restante ou une limite journalière atteinte, les
appels suivants sont bloqués localement jusqu'à minuit UTC. Avant chaque
requête de données non mise en cache, un ledger SQLite UTC réserve atomiquement
un token via `BEGIN IMMEDIATE`. Les processus qui utilisent le même fichier
SQLite ne peuvent donc pas dépenser le même token ; les réservations réseau
échouées sont conservées de façon prudente, et les snapshots quota plus récents
ne réaugmentent pas le budget du jour. La réserve d'urgence reste protégée pour
les appels secondaires ; si le quota est inconnu, seuls les appels critiques
peuvent utiliser un petit budget configurable.
Ce ledger ne coordonne pas encore les fichiers SQLite séparés de Streamlit Cloud
et GitHub Actions : les leases distantes continuent de sérialiser les
synchronisations exhaustives, mais les appels live indépendants entre ces deux
environnements peuvent encore se concurrencer. Une allocation distante globale
de quota reste une amélioration à venir.
Les réponses identiques demandées simultanément ou dans les 60 secondes sont
également partagées entre tous les clients du même processus sans nouvel appel
réseau. Ce délai peut être réglé avec `API_FOOTBALL_REQUEST_CACHE_SECONDS`.

L'audit endpoint par endpoint et les exceptions autorisées sont documentés dans
[`docs/api-request-audit.md`](docs/api-request-audit.md).

### Supported database

SQLite is the only officially supported database engine. Some synchronization
queries intentionally use SQLite features such as `datetime(...)`, `PRAGMA`
and `ON CONFLICT`. Set `DATABASE_URL` to a SQLite URL (the default is
`sqlite:///football.db`). Other SQLAlchemy engines are not currently supported.

La base est stockée localement dans `football.db` par défaut. Configurez
`DATABASE_URL` pour choisir un autre chemin SQLite. Sur Streamlit Community
Cloud, une copie SQLite compressée est installée dans `/tmp/football.db` au
premier démarrage si aucune base n'existe. Les imports et les modifications
restent locaux : aucune base distante n'est contactée. Streamlit ne garantit
pas la persistance des fichiers locaux et peut les supprimer à tout moment ;
après une réinitialisation, l'application repartira de l'instantané fourni.
Consultez
[`docs/backup-restore.md`](docs/backup-restore.md) pour la sauvegarde.

## Checks

```bash
python -m unittest discover -s tests -v
python -m compileall -q app.py components database pages services scripts tests
python -m pip check
```

## Reproducible backtest

```bash
python -m scripts.run_backtest --start-season 2024 --min-prior-matches 30
```

The JSON output compares the former formula, the draw-rate formula and the
API-Football blend on its strictly comparable subset. It includes 1/N/2
accuracy, multiclass Brier score, log loss, draw-specific measures and a SHA-256
fingerprint of the database rows used. Historical context and API data are
accepted only when timestamped strictly before kickoff.

To evaluate predictions that were actually saved by the application (including
the idempotent import of legacy analysis snapshots), run:

```bash
python -m scripts.run_backtest --persisted-predictions
```

This mode keeps current internal predictions and legacy snapshots separated by
model version/source, excludes forecasts saved at or after kickoff, and reports
accuracy, Brier score, log loss, calibration and draw-specific metrics.

### Calibration de la probabilité de nul

Le pipeline Poisson applique une calibration logistique dédiée au nul
(`draw-logistic-v1-2016-2025`). Elle utilise la probabilité Poisson initiale,
l'écart et le total de buts projetés ainsi que les probabilités des scores 0-0
et 1-1. La masse restante est redistribuée proportionnellement entre domicile
et extérieur ; la matrice des scores et la distribution 1/N/2 restent cohérentes.
La calibration ne force donc pas artificiellement le nul comme premier choix.

Le modèle a été évalué chronologiquement sur six compétitions, sans utiliser
les résultats futurs : entraînement jusqu'à 2023 puis test 2024, jusqu'à 2024
puis test 2025, enfin jusqu'à 2025 puis test 2026. Le Brier multiclasses est
passé de 0.6112 à 0.6108 (2024), de 0.6095 à 0.6083 (2025) et de 0.5914 à
0.5906 (2026). Le Log Loss s'améliore également sur les trois périodes. Les
gains sont modestes mais réguliers ; la calibration est versionnée et reste
séparée du choix du favori.

## xG historiques

La page « Mise à jour » peut télécharger les statistiques de matchs terminés
depuis l'endpoint API-Football `/fixtures/statistics`. Les valeurs
`expected_goals` et `goals_prevented` sont conservées par match et par équipe
dans `fixture_team_statistics`. La synchronisation est incrémentale, reprend
après une limite de quota et distingue les réponses téléchargées des matchs où
le fournisseur publie effectivement des xG.

Avant chaque appel, la synchronisation effectue une différence avec la base :
un match n'est complet que si les xG des deux équipes sont présents. Les matchs
complets sont ignorés sans appel API. Une réponse sans xG est mémorisée ; elle
peut être contrôlée pendant les 72 heures suivant le match, puis n'est plus
redemandée automatiquement afin de préserver le quota. La synchronisation
exhaustive traite les données des rencontres en cours et à venir avant le
rattrapage xG historique.
`XG_PUBLICATION_GRACE_HOURS` permet d'ajuster cette fenêtre (72 heures par
défaut, minimum 24 heures).

Les écrans d'analyse présentent les moyennes récentes xG, xGA et leur
différentiel. Pour une rencontre à venir, seules les lignes dont la date est
strictement antérieure au coup d'envoi sont chargées. Les sorties du modèle
Poisson sont libellées « buts projetés » afin de ne pas les confondre avec les
xG observés fournis par l'API.

Le glossaire complet des indicateurs, de leurs calculs et de leurs limites est
disponible dans [`docs/statistics-glossary.md`](docs/statistics-glossary.md).
La même documentation est présentée sous forme de légende contextuelle dans
chacun des six onglets d’une prédiction.

### Sauvegarde et restauration

Le code, la base SQLite locale et les secrets sont sauvegardés séparément. La
procédure de copie et de restauration est documentée dans
[`docs/backup-restore.md`](docs/backup-restore.md).

Chaque tentative xG génère également une ligne immuable dans
`xg_ingestion_audit` avec un identifiant de lot, la fixture, l'endpoint et les
paramètres sans secret, les heures de début et de fin, le statut, la réponse
brute et son empreinte SHA-256. Les lignes courantes de
`fixture_team_statistics` référencent l'audit qui les a produites. Des triggers
SQLite interdisent la modification ou la suppression du journal et valident
les liens d'ingestion. Le journal récent est consultable depuis la page
« Mise à jour ».

## Analyse complète du projet

### 1) État actuel du dépôt
- Le dépôt contient désormais une application Streamlit orientée analyse/pronostic football, avec services, pages, composants, scripts et tests.
- La documentation inclut les prérequis, l’exécution locale, les vérifications et le backtest reproductible.

### 2) Objectif produit
Fournir une application d’aide à la décision football combinant données historiques/statistiques et signaux API, avec une restitution claire des tendances et estimations 1/N/2.

### 3) Écart entre vision et implémentation
- **Vision** : système de pronostic IA robuste, explicable et suivi dans le temps.
- **Implémentation actuelle** : base fonctionnelle solide (UI, persistance, synchronisation API, contrôles), avec logique d’estimation encore explicitement expérimentale.
- **Conclusion** : le projet est en phase de consolidation vers un moteur de prédiction plus calibré.

### 4) Architecture cible recommandée
- **Ingestion de données** : résultats, calendriers, stats équipes/joueurs, signaux API externes.
- **Feature engineering** : forme, domicile/extérieur, H2H, dynamiques offensives/défensives, temporalité stricte.
- **Modélisation IA** : baseline versionnée, validation temporelle, calibration probabiliste.
- **Service de prédiction** : pipeline d’inférence traçable et endpoints stables.
- **Interface utilisateur** : visualisation des probabilités, facteurs explicatifs, statut des données.
- **Observabilité** : métriques de qualité, suivi de dérive, audit des runs.

### 5) Risques principaux
- Qualité, fraîcheur et couverture des données sportives.
- Fuite de données temporelles (utilisation de données futures dans l’entraînement).
- Surapprentissage sur des contextes de saison/ligue limités.
- Écart entre performance offline et robustesse en production.

### 6) Priorités de démarrage MVP
1. Formaliser le périmètre fonctionnel (compétitions, fréquence, type de sorties).
2. Stabiliser un pipeline de données versionné et reproductible.
3. Définir une baseline IA mesurable (Brier, log loss, accuracy 1/N/2).
4. Encadrer la calibration et l’explicabilité des probabilités.
5. Renforcer les contrôles CI/tests autour des parcours critiques.
6. Documenter clairement les limites métier et techniques.

### 7) Critères de succès MVP
- Exécution bout en bout reproductible (données → calcul → sortie).
- Métriques suivies dans le temps avec seuils d’alerte.
- API/UX cohérentes, stables et documentées.
- Traçabilité des versions de données, règles et modèles.
