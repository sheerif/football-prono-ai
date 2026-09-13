# Instantané de démarrage

`football-cache-v3.db.gz` est une réplique SQLite Turso vérifiée et compressée.
Streamlit la décompresse par blocs de 1 Mio dans `/tmp`, puis Turso Sync ne
récupère que les changements postérieurs à cet instantané. Les lectures de
l'interface restent ainsi locales et ne consomment plus le quota « rows read ».

Le fichier `football-cache-v3.db-info.json` contient uniquement la révision de
synchronisation et l'URL publique de la base. L'identifiant du client est
remplacé par une valeur aléatoire à chaque nouveau conteneur Streamlit. Aucun
jeton d'authentification n'est stocké dans ces fichiers.
