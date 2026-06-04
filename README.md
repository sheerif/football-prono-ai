# football-prono-ai

Application de pronostic IA.

## Analyse complète du projet

### 1) État actuel du dépôt
- Le dépôt contient actuellement un seul fichier de documentation (`README.md`).
- Il n’y a pas encore de code applicatif, de structure de dossiers métier, ni de configuration d’outillage (build, tests, CI/CD).

### 2) Objectif produit (déduit du nom du projet)
Créer une application capable de proposer des pronostics de football à l’aide de l’IA, avec une logique de collecte de données, d’entraînement/inférence et d’exposition des prédictions.

### 3) Écart entre vision et implémentation
- **Vision** : plateforme de prédiction football.
- **Implémentation actuelle** : initialisation minimale du dépôt.
- **Conclusion** : le projet est en phase de cadrage/amorçage.

### 4) Architecture cible recommandée
- **Ingestion de données** : résultats historiques, calendriers, cotes, statistiques d’équipes/joueurs.
- **Feature engineering** : forme récente, domicile/extérieur, historiques de confrontations, fatigue/calendrier.
- **Modélisation IA** : pipeline d’entraînement, validation temporelle, calibration des probabilités.
- **Service de prédiction** : API (ex. endpoint par match) + versionnement des modèles.
- **Interface utilisateur** : consultation des pronostics, confiance, explications simples.
- **Observabilité** : suivi qualité modèle, dérive de données, traçabilité des runs.

### 5) Risques principaux
- Qualité et disponibilité des données sportives.
- Fuite de données temporelles dans l’entraînement.
- Surapprentissage sur des saisons spécifiques.
- Dégradation de performance en production sans monitoring.

### 6) Priorités de démarrage
1. Définir le périmètre fonctionnel MVP (ligues, types de pronostics, fréquence).
2. Mettre en place la structure de base du repo (src, tests, config).
3. Intégrer un premier pipeline de données reproductible.
4. Implémenter un modèle baseline et ses métriques.
5. Exposer une API minimale de prédiction.
6. Ajouter CI, tests et documentation technique.

### 7) Critères de succès MVP
- Pipeline exécutable de bout en bout (données → entraînement → prédiction).
- Métriques explicites et suivies dans le temps.
- API stable documentée.
- Reproductibilité des résultats (version de données et modèle).
