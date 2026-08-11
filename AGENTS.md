# Football Prono AI — règles de décision

Toute évolution du moteur de pronostic doit respecter les règles détaillées
dans [docs/decision-engine-policy.md](docs/decision-engine-policy.md).

Avant de modifier la logique métier de prédiction :

1. inspecter les services et les tests existants ;
2. produire un plan d’implémentation fichier par fichier ;
3. préserver les interfaces, la base, les imports et la rétrocompatibilité ;
4. ajouter ou adapter les tests avant de déclarer le changement terminé ;
5. effectuer un backtest comparant l’ancien et le nouveau comportement.

Principes non négociables :

- une distribution 1/N/2 unique, normalisée à 100 %, est la source de vérité ;
- les doubles chances sont uniquement dérivées de 1/N/2 ;
- le score exact et 1/N/2 sont issus de la même distribution de scores ;
- probabilité, consensus, solidité, risque et recommandation sont des notions
  distinctes et ne doivent jamais être confondues dans le code ou l’interface ;
- aucune donnée postérieure au coup d’envoi ne peut influencer une prédiction ;
- l’API et les IA sont des signaux bornés et explicables : elles ne remplacent
  jamais brutalement le modèle statistique ;
- une divergence importante doit conduire à la prudence, jamais à une victoire
  forcée ;
- les pages affichent les résultats du moteur central et ne recalculent pas les
  probabilités localement.
