# Sauvegarde et restauration

L'application utilise une base SQLite locale. Par défaut, le fichier est
`football.db` à la racine du projet. Il est ignoré par Git et ne doit pas être
ajouté au dépôt.

## Créer une sauvegarde cohérente

Arrêtez l'application ou utilisez l'API de sauvegarde SQLite depuis un script,
puis copiez le fichier et ses éventuels fichiers WAL ensemble. Par exemple,
lorsque l'application est arrêtée :

```bash
cp football.db "football-backup-$(date +%F).db"
```

Pour une copie pendant que l'application tourne, utilisez la commande de
sauvegarde SQLite (`Connection.backup`) plutôt qu'une copie simple du fichier.

## Restaurer

1. Arrêtez l'application.
2. Conservez une copie du fichier courant, puis placez la sauvegarde sous le
   nom indiqué par `DATABASE_URL` (par défaut `football.db`).
3. Redémarrez l'application et vérifiez les volumes depuis la page « Mise à
   jour ».

Le système de fichiers de Streamlit Community Cloud est éphémère. Pour ne pas
perdre les modifications au redémarrage, l'application peut envoyer une copie
SQLite compressée sur GitHub après chaque import ou synchronisation, y compris
quand une synchronisation s'arrête sur le quota API.

Après chaque lot d'écritures, SQLite actualise aussi les statistiques de ses
requêtes et effectue un checkpoint WAL non bloquant. Si les pages inutilisées
dépassent 2 048 pages et 5 % de la base, une compaction `VACUUM` est lancée
après le lot. Une base peu fragmentée n'est pas réécrite inutilement.

## Activer la sauvegarde Git automatique

Dans les secrets Streamlit, ajoutez un jeton GitHub avec le droit **Contents:
Read and write** sur le dépôt `sheerif/football-prono-ai` :

```toml
GITHUB_BACKUP_TOKEN = "github_pat_..."
```

Le dépôt cible est public : les copies de la base seront donc publiques. Par
défaut, elles sont publiées une fois par jour dans la branche
`database-backup`, sous `football.db.gz` pour les petites archives, ou en fragments de 8 Mio accompagnés de `football.db.manifest.json` pour les archives volumineuses. Une vérification non bloquante tourne
à l'ouverture de l'application puis chaque heure. Les nouvelles copies sont conservées dans l'historique de cette branche.
La synchronisation planifiée n'en publie qu'une par jour (elle n'impose plus de
sauvegarde à chaque exécution horaire). Une fois par mois, au premier snapshot
modifié, l'historique de la branche est réinitialisé à un commit racine tout en
conservant le snapshot courant : cela limite l'historique accessible à environ
un mois de sauvegardes quotidiennes. Les objets Git devenus inaccessibles
peuvent rester comptabilisés jusqu'au nettoyage/GC de GitHub ; cette rotation
borne l'historique référencé mais ne garantit pas une réduction immédiate de la
taille physique du dépôt. Le bouton de sauvegarde manuelle reste forcé. Pour
changer la destination, configurez aussi `GITHUB_BACKUP_REPOSITORY` (`proprietaire/depot`)
et éventuellement `GITHUB_BACKUP_BRANCH` (`database-backup`).

La sauvegarde quotidienne démarre en arrière-plan. Une tâche d'import ou de
synchronisation peut aussi déclencher une copie si aucune n'a encore été faite
ce jour-là. Le bouton « Créer une sauvegarde maintenant » force une vérification
immédiate et ne crée pas de commit si la base est inchangée. La page « Mise à
jour » indique si le jeton est configuré.
Une erreur GitHub apparaît dans le détail de la tâche : elle ne supprime ni
n'interrompt les données conservées localement. Vérifiez périodiquement que la branche contient `football.db.gz` ou le manifeste et tous ses fragments, et conservez aussi une copie téléchargée sur un autre support.

Pour restaurer, récupérez `football.db.gz` directement si présent. Pour une archive fragmentée, reconstituez-la dans l'ordre indiqué par `football.db.manifest.json` et vérifiez les tailles et empreintes SHA-256, puis décompressez-la. Le workflow planifié effectue automatiquement cette reconstitution et ces contrôles.

Ne sauvegardez jamais `.env`, les mots de passe ou les clés API dans Git ou
une sauvegarde non chiffrée. Un secret exposé doit être révoqué puis remplacé.
