# Politique du moteur de décision

## Mission

Faire évoluer Football Prono AI vers un moteur de décision probabiliste,
cohérent, explicable, robuste et vérifiable par backtesting. Ne pas modifier
des calculs simplement parce qu’un résultat semble meilleur sur quelques
matchs : chaque changement doit renforcer l’architecture et être évalué
historiquement.

## Contrat probabiliste

- La distribution fondamentale est unique : `P1`, `PN`, `P2`, avec une somme
  égale à 100 %.
- `confidence` est uniquement la probabilité du scénario principal.
- Les doubles chances sont dérivées exclusivement de cette distribution :
  `1X = P1 + PN`, `X2 = PN + P2`, `12 = P1 + P2`.
- Le score exact et le 1/N/2 doivent provenir d’une même matrice de scores
  (Poisson ou modèle statistiquement équivalent). La somme des cellules avec
  domicile supérieur, égal ou inférieur à l’extérieur doit redonner `P1`,
  `PN`, `P2`.

## Signaux et décision centrale

Le modèle statistique est le socle. IA 1 est l’analyse principale ; IA 2 est
une contre-analyse indépendante. Elles fournissent si possible leur
distribution 1/N/2, score, marchés, confiance et fiabilité. Elles ne sont pas
deux votes à moyenner. Le système mesure leur accord, la distance entre leurs
distributions, leur fiabilité historique et la qualité des données.

Un Decision Engine central reçoit le modèle, les IA, l’historique, la forme,
les forces attaque/défense, domicile/extérieur, H2H, joueurs, tactique, API et
la qualité des données. Il produit : distribution finale, score probable,
doubles chances, consensus, solidité, risque, marché recommandé et une
explication. Les pages ne portent pas cette logique métier.

## API et recommandations

Conserver séparés :

- `API_1X2_PROBABILITIES`, signal probabiliste éventuellement fusionné avec un
  poids plafonné et configurable ;
- `API_MARKET_ADVICE`, signal de marché et de convergence uniquement.

Un conseil API `1X`, `X2` ou `12` ne doit jamais être converti en fausse
distribution 1/N/2. Les poids doivent à terme pouvoir varier selon le
championnat, le marché, la disponibilité des données et les performances
historiques, sans être entraînés sur le même échantillon que le backtest final.

## Solidité, risque et qualité

La solidité (`0–100`) n’est jamais une probabilité. Elle reflète notamment la
marge entre les deux premiers scénarios, la qualité et la fraîcheur des
données, l’historique, la stabilité entre fenêtres, l’accord des sources et
leur fiabilité. Le risque combine cette marge avec les divergences, absences,
manques de données et contradictions. Une marge faible ou une divergence forte
doit permettre `PRUDENCE / PAS DE PARI` ou une double chance justifiée : ne pas
forcer une victoire marginalement en tête.

Les informations joueurs et tactique restent des ajustements plafonnés, nuls
en cas de données insuffisantes. Les données manquantes réduisent la qualité ;
elles ne sont jamais inventées.

## Intégrité, explication et validation

Pour un match à la date D, utiliser exclusivement les informations connues
avant D. Protéger cette règle contre la fuite de données par tests.

Toute sortie finale explique le scénario, la marge, le consensus, la qualité,
le risque et le marché. Les métriques de validation couvrent au minimum :
accuracy, Brier Score, Log Loss, ECE, calibration curve, performances par
ligue, issue et marché, doubles chances et tranches de solidité/ranking.

Tester systématiquement : normalisation 1/N/2, doubles chances, cohérence de
la matrice de scores, signaux API valides/neutres/invalides, convergence et
divergence IA, données incomplètes, data leakage, reproductibilité et matchs
équilibrés. Comparer ancien et nouveau moteur par backtest avant d’affirmer une
amélioration.

Priorités en cas de conflit : cohérence mathématique, absence de data leakage,
qualité statistique, robustesse, explicabilité, puis compatibilité applicative.
