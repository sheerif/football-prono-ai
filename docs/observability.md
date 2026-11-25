# Observabilité et centralisation des logs

## Sources de logs

L'application utilise déjà `services/trace_runtime.py` pour produire des
événements structurés (JSON) : début/fin de fonction, durée, exceptions,
identifiants de trace et contexte opérationnel autorisé. Les valeurs des
arguments ne sont pas journalisées. Les champs sensibles et les secrets sont
masqués par le traceur.

Le traceur Python écrit via le système standard `logging`. Les logs sont donc
émis sur la sortie du processus et récupérés par la plateforme qui exécute le
processus.

## GitHub Actions : synchronisations planifiées

Le workflow `.github/workflows/scheduled-sync.yml` conserve maintenant une
copie complète de la sortie de la synchronisation dans
`scheduled-sync.log`. Le fichier contient les événements structurés émis par
le traceur ainsi que les messages opérationnels du script.

Le fichier est publié comme artefact GitHub Actions, y compris si l'étape de
synchronisation échoue :

- Nom : `scheduled-sync-logs-<run_id>`
- Rétention : 14 jours
- Publication : étape exécutée avec `if: always()`
- Échec du script conservé : `pipefail` transmet bien le code retour de Python
  malgré l'utilisation de `tee`

Pour consulter un journal : ouvrir le dépôt GitHub, aller dans **Actions**,
ouvrir l'exécution du workflow **Scheduled priority data sync and historical
backfill**, puis télécharger l'artefact de logs associé.

Les journaux natifs du job restent également disponibles dans l'interface
GitHub Actions.

## Streamlit Community Cloud

Les logs du processus Streamlit sont visibles dans **Manage app → Logs**.
Ils comprennent les événements du traceur Python lorsque
`FOOTBALL_FUNCTION_TRACE=1` est activé.

Ces logs Streamlit ne sont pas automatiquement accessibles à GitHub Actions :
les deux environnements sont séparés et le système de fichiers de Streamlit
Community Cloud est éphémère. Le présent changement ne prétend donc pas
transférer les logs Streamlit vers GitHub.

Pour une centralisation réellement commune entre Streamlit Cloud et GitHub,
il faut connecter un collecteur externe (par exemple un service de logs
compatible HTTP/OTLP), définir sa politique de rétention et configurer son
URL/jeton dans les secrets Streamlit et GitHub Actions. Cette intégration doit
être faite avec un secret dédié et une file tampon bornée, des délais courts,
des tentatives limitées et un mode dégradé non bloquant. Ne jamais écrire les
secrets, données d'authentification, en-têtes HTTP ou corps de requêtes dans
les journaux.

## Pourquoi les logs ne sont pas commités dans main

Les journaux d'exécution sont des données opérationnelles, pas du code source.
Les commiter à chaque événement créerait du bruit, des conflits et une
croissance inutile de l'historique Git. Les artefacts Actions fournissent un
stockage consultable et limité dans le temps sans modifier la branche
applicative.
