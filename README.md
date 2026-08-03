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

### Supported database

SQLite is the only officially supported database engine. Some synchronization
queries intentionally use SQLite features such as `datetime(...)`, `PRAGMA`
and `ON CONFLICT`. Set `DATABASE_URL` to a SQLite URL (the default is
`sqlite:///football.db`). Other SQLAlchemy engines are not currently supported.

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
