# Sauvegarde et restauration

L'état récupérable du site repose sur trois éléments distincts :

1. le code, conservé dans Git et figé par un tag ;
2. les données, conservées dans la base Turso et dans une copie indépendante ;
3. les secrets Streamlit/GitHub, conservés dans les interfaces sécurisées des
   plateformes et jamais dans le dépôt.

## Sauvegarder la base Turso

Le script suivant crée une nouvelle base Turso à partir de la base de
production, puis vérifie les volumes des tables principales :

```bash
TURSO_CLI=/home/sheerif/.turso/turso \
  TURSO_SOURCE_DATABASE=football-prono \
  ./scripts/backup_turso.sh
```

On peut imposer un nom explicite :

```bash
TURSO_BACKUP_DATABASE=football-prono-backup-avant-migration \
  ./scripts/backup_turso.sh
```

Cette copie ne lance aucun appel API-Football et ne consomme donc pas son
quota. Une copie Turso compte toutefois dans le nombre de bases autorisé par le
forfait. Il est recommandé d'en créer une avant chaque migration sensible et de
conserver au moins une copie datée validée.

Turso propose également une récupération à un instant précis. Elle crée une
nouvelle base sans écraser la base actuelle :

```bash
turso db create football-prono-recovery \
  --from-db football-prono \
  --timestamp 2026-09-10T10:00:00Z \
  --wait
```

La durée d'historique disponible dépend du forfait Turso.

## Figer le code

Un tag Git permet de retrouver exactement la version de l'application associée
à une sauvegarde :

```bash
git tag -a backup-YYYY-MM-DD -m "Sauvegarde stable YYYY-MM-DD"
git push origin backup-YYYY-MM-DD
```

Pour examiner cette version sans modifier `main` :

```bash
git switch -c recovery/backup-YYYY-MM-DD backup-YYYY-MM-DD
```

## Restaurer le site

1. Créer une nouvelle base depuis la copie ou depuis un point dans le temps.
2. Générer un jeton pour cette nouvelle base avec
   `turso db tokens create <nouvelle-base>`.
3. Remplacer `TURSO_DATABASE_URL` et `TURSO_AUTH_TOKEN` dans les secrets
   Streamlit Cloud et GitHub Actions.
4. Redémarrer l'application et contrôler le tableau de bord avant toute reprise
   de synchronisation.
5. Conserver l'ancienne base tant que la restauration n'est pas validée.

Ne jamais copier les jetons, mots de passe ou clés API dans Git, une capture
d'écran ou un fichier de sauvegarde non chiffré. Un secret exposé doit être
révoqué puis remplacé.

## Contrôle minimal après restauration

```sql
SELECT COUNT(*) FROM matches;
SELECT COUNT(*) FROM teams;
SELECT COUNT(*) FROM players;
SELECT COUNT(*)
FROM fixture_team_statistics
WHERE expected_goals IS NOT NULL;
SELECT COUNT(*) FROM api_response_archive;
```

Ces compteurs doivent être cohérents avec ceux affichés lors de la création de
la sauvegarde. Un léger écart avec la production est normal si une
synchronisation écrivait encore pendant la copie.
