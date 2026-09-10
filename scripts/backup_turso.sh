#!/usr/bin/env bash
set -euo pipefail

# Crée une copie Turso indépendante sans appeler API-Football.
# Le CLI Turso doit être installé et authentifié (`turso auth login`).

TURSO_CLI="${TURSO_CLI:-turso}"
SOURCE_DATABASE="${TURSO_SOURCE_DATABASE:-football-prono}"
BACKUP_TIMESTAMP="$(date -u +%Y%m%d-%H%M%S)"
BACKUP_DATABASE="${TURSO_BACKUP_DATABASE:-${SOURCE_DATABASE}-backup-${BACKUP_TIMESTAMP}}"

if ! command -v "$TURSO_CLI" >/dev/null 2>&1 && [[ ! -x "$TURSO_CLI" ]]; then
  echo "Erreur : CLI Turso introuvable. Définissez TURSO_CLI avec son chemin complet." >&2
  exit 1
fi

if (( ${#BACKUP_DATABASE} > 64 )); then
  echo "Erreur : le nom de sauvegarde dépasse la limite Turso de 64 caractères." >&2
  exit 1
fi

echo "Création de la sauvegarde '$BACKUP_DATABASE' depuis '$SOURCE_DATABASE'..."
"$TURSO_CLI" db create "$BACKUP_DATABASE" --from-db "$SOURCE_DATABASE" --wait

echo "Contrôle des tables principales..."
"$TURSO_CLI" db shell "$BACKUP_DATABASE" \
  "SELECT
     (SELECT COUNT(*) FROM matches) AS matches,
     (SELECT COUNT(*) FROM teams) AS teams,
     (SELECT COUNT(*) FROM players) AS players,
     (SELECT COUNT(*) FROM fixture_team_statistics WHERE expected_goals IS NOT NULL) AS xg_rows,
     (SELECT COUNT(*) FROM api_response_archive) AS archived_responses;"

echo "Sauvegarde terminée : $BACKUP_DATABASE"
