# Audit de consommation des requêtes API-Football

Date de l'audit : 7 septembre 2026.

## Règle appliquée

Une requête réseau n'est justifiée que si la donnée métier est absente,
incomplète, arrivée à échéance parce qu'elle évolue encore, ou si l'utilisateur
demande explicitement un rafraîchissement forcé. La comparaison doit avoir lieu
avant l'appel API, pas après réception de la réponse.

## Résultat par endpoint

| Endpoint | Utilité | Contrôle avant appel | Politique retenue |
|---|---|---|---|
| `/leagues` | Métadonnées d'une compétition | nom et logo en base | Appel uniquement si les métadonnées manquent. |
| `/teams` | Équipes d'une saison | équipes présentes pour la ligue | Aucun nouveau téléchargement lorsqu'elles sont déjà disponibles. Les équipes découvertes dans `/fixtures` sont aussi enregistrées. |
| `/fixtures` par ligue/saison | Calendrier, résultats et détails | saison présente et caractère historique/actif | Historique complet ignoré. Saison active rafraîchie selon l'intervalle configuré. La même réponse alimente désormais `matches` et `fixture_api_details`. |
| `/standings` | Classement | classement présent et saison active/historique | Historique présent ignoré. Classement actif actualisé périodiquement. |
| `/predictions` | Conseil API pré-match | ligne `fixture_api_predictions` et registre persistant | Une requête par match réellement manquant. Une réponse non publiée est différée 12 heures. |
| `/fixtures/statistics` | Statistiques du match et xG | xG des deux équipes présents | Une requête uniquement si les xG complets manquent. Réponses non publiées temporisées ; anciens matchs non redemandés indéfiniment. |
| `/fixtures/lineups` | Composition officielle | deux compositions présentes | Donnée complète ignorée. Indisponibilité temporisée par le registre lors de la synchronisation exhaustive. |
| `/fixtures/players` | Performances individuelles | statistiques déjà présentes | Match complet ignoré. Indisponibilité récente temporisée. |
| `/players` | Profils et statistiques paginés | périmètre ligue/saison enregistré comme complet | La synchronisation exhaustive ignore un périmètre complet. Le bouton manuel reste une demande explicite de rafraîchissement. |

## Anomalies constatées et corrigées

1. L'import appelait `/leagues` avant de constater qu'une saison historique
   était déjà complète.
2. Lorsqu'un seul bloc manquait, équipes, matchs et classement pouvaient être
   retéléchargés ensemble. Ils sont désormais contrôlés séparément.
3. La réponse `/fixtures` déjà payée n'alimentait pas la table de détails ; cela
   pouvait provoquer ensuite un appel `/fixtures?id=...` pour chaque match.
4. Le démarrage rafraîchissait la saison courante, puis pouvait la forcer une
   seconde fois dans le passage historique. La saison venant d'être actualisée
   est maintenant exclue du second rafraîchissement forcé.
5. Les prédictions non encore publiées et les détails durablement incomplets
   pouvaient être redemandés à chaque réaffichage de page. Leur indisponibilité
   est désormais persistée et temporisée.
6. Plusieurs composants pouvaient demander exactement le même endpoint et les
   mêmes paramètres presque simultanément. Les réponses sont partagées pendant
   60 secondes et les appels réseau sont sérialisés.
7. Après épuisement quotidien, les autres clients continuaient à tenter des
   appels. Un coupe-circuit commun bloque maintenant tout nouvel appel réseau
   jusqu'à minuit UTC.

## Mesure de la base auditée

Le fichier local audité contient 22 143 matchs, dont 20 268 terminés et 1 875
sans résultat, sur 6 ligues configurées de 2016 à 2026. Il contient 1 816 caches
de détails complets et toutes les prédictions futures présentes au moment de la
mesure. Ces nombres décrivent la copie locale et peuvent différer de la base du
déploiement.

## Risques résiduels

- Le coupe-circuit et le cache de réponse sont partagés dans un processus. Un
  redémarrage ou plusieurs instances peuvent encore produire une première
  tentative chacune ; un verrou distribué en base serait nécessaire pour une
  garantie multi-instance stricte.
- La traçabilité détaillée de chaque appel réseau n'est append-only que pour les
  xG. Les autres familles exposent des totaux par tâche, mais pas encore un
  journal central par endpoint et empreinte de paramètres.
- Les actions explicitement forcées par l'utilisateur peuvent légitimement
  redemander une donnée déjà présente. Elles doivent rester clairement
  identifiées dans l'interface.
