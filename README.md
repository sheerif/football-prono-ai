# Prono insight

Application Streamlit d’aide à l’analyse football avec dashboard, imports
API-Football, widgets API-Sports, statistiques, estimations 1/N/2 expérimentales
et suivi persistant des mises à jour.

Les scores 1/N/2 sont des estimations internes issues de règles statistiques.
Ils ne constituent ni une fiabilité calibrée ni un conseil de pari.

Lorsqu’un conseil API-Football informatif est disponible pour une rencontre
programmée, l’analyse lui attribue un poids adaptatif et minoritaire de 10 à
30 %, selon sa précision et la richesse des comparaisons fournies. Les réponses
neutres 33/33/33 sont ignorées. Les probabilités, l’accord ou le désaccord et
les comparaisons API (forme, attaque, défense, Poisson, face-à-face et buts)
restent visibles. La page « Mise à jour » synchronise tous les conseils futurs
sans retélécharger ceux déjà enregistrés.

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

The live widget uses `API_FOOTBALL_WIDGET_KEY` when configured, otherwise it
falls back to `API_FOOTBALL_KEY`. Because the widget runs in the browser, the
selected key is visible to authenticated users. Use a separate key restricted
by domain and quota if that exposure becomes a concern.

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

Les xG passent en premier, avec une réserve quotidienne de 1 500 requêtes par
défaut pour les matchs courants, classements, compositions, prédictions et
joueurs. Cette réserve se règle avec `XG_DAILY_RESERVE`. Une fois ce seuil
atteint, le rattrapage xG s'interrompt pour la journée et la synchronisation
continue sur les données courantes. `FULL_SYNC_DAILY_RESERVE` permet en plus de
conserver une réserve en fin de synchronisation (valeur par défaut : `0`).
La limite du fournisseur reste incontournable ; lorsqu'elle est atteinte, la
progression est conservée jusqu'à minuit UTC.
Tous les clients API partagent aussi un coupe-circuit en mémoire : dès qu'une
réponse indique zéro requête restante ou une limite journalière atteinte, les
appels suivants sont bloqués localement jusqu'à minuit UTC. Un verrou commun
évite que plusieurs tâches concurrentes dépassent la limite en même temps.
Les réponses identiques demandées simultanément ou dans les 60 secondes sont
également partagées entre tous les clients sans nouvel appel réseau. Ce délai
peut être réglé avec `API_FOOTBALL_REQUEST_CACHE_SECONDS`.

Le workflow GitHub Actions `Synchronisation quotidienne` lance également la
synchronisation à 00 h 05 UTC, même lorsque Streamlit Cloud est endormi. Il
nécessite les secrets GitHub `API_FOOTBALL_KEY`, `TURSO_DATABASE_URL` et
`TURSO_AUTH_TOKEN`. Le verrou persistant `full-sync:exhaustive` empêche ce
workflow de doubler une reprise déjà lancée par Streamlit.

L'audit endpoint par endpoint et les exceptions autorisées sont documentés dans
[`docs/api-request-audit.md`](docs/api-request-audit.md).

### Supported database

SQLite is the only officially supported database engine. Some synchronization
queries intentionally use SQLite features such as `datetime(...)`, `PRAGMA`
and `ON CONFLICT`. Set `DATABASE_URL` to a SQLite URL (the default is
`sqlite:///football.db`). Other SQLAlchemy engines are not currently supported.

Lorsque Turso est activé, le mode par défaut est `direct`. C'est le mode adapté
à Streamlit Community Cloud : il ne charge pas la réplique complète d'environ
180 Mo en mémoire. Les résultats lourds de l'interface sont gardés cinq minutes
dans le cache Streamlit. Une lecture indexée d'une seule ligne vérifie toutes
les quinze secondes si une mise à jour a terminé ; le cache est invalidé et la
page rechargée uniquement lorsque ce marqueur change.

Le mode `TURSO_ACCESS_MODE=replica` reste disponible sur un PC ou un runner
disposant d'assez de mémoire. Il copie alors la base dans
`football-cache-v2.db`, lit localement et pousse les écritures vers Turso. Le
workflow GitHub de minuit utilise cette réplique locale et la conserve dans le
cache GitHub entre deux exécutions. `STREAMLIT_FORCE_DIRECT_DATABASE=true`
neutralise aussi un ancien secret `replica` encore présent sur Streamlit Cloud.
Sur Streamlit, les mises à jour au démarrage sont désactivées par défaut
(`STREAMLIT_STARTUP_UPDATES=false`) afin de ne pas doubler ce traitement
planifié ni consommer inutilement la RAM et le quota API.

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
exhaustive traite les xG manquants en priorité avant les autres endpoints.
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

Le code, la base Turso et les secrets sont sauvegardés séparément. La procédure
de copie distante, de contrôle et de restauration est documentée dans
[`docs/backup-restore.md`](docs/backup-restore.md). Le script
`scripts/backup_turso.sh` crée une copie indépendante de la base sans consommer
de requête API-Football.

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
