# Sauvegarde et restauration locale

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

Le système de fichiers de certains hébergeurs, notamment Streamlit Community
Cloud, est éphémère. Une base locale qui y est modifiée peut être perdue au
redémarrage ou au redéploiement. Gardez une sauvegarde externe régulière si
vous hébergez l'application dans cet environnement.

## Figer le code

Un tag Git permet de retrouver la version associée à une sauvegarde :

```bash
git tag -a backup-YYYY-MM-DD -m "Sauvegarde stable YYYY-MM-DD"
git push origin backup-YYYY-MM-DD
```

Ne sauvegardez jamais `.env`, les mots de passe ou les clés API dans Git ou
une sauvegarde non chiffrée. Un secret exposé doit être révoqué puis remplacé.
