import logging
import streamlit as st
import datetime
import os

from services import api_football, background_jobs, git_backup, import_service, live_priority_watcher, schema_guard
from components import api_metrics, auth, sidebar, ui
from pages import dashboard, data_management, api_widgets, matchs_a_venir, analyse_match, prediction_ia

logger = logging.getLogger(__name__)

st.set_page_config(page_title="Prono insight", layout="wide", initial_sidebar_state="auto")
ui.inject_app_style()

if not auth.is_authenticated():
	auth.login_page()
	st.stop()

@st.cache_resource(show_spinner="Connexion à la base de données…")
def _init_db_once():
	import_service.init_db()


try:
	_init_db_once()
	schema_guard.ensure_match_score_columns()
except Exception:
	st.error(
		"Initialisation de la base SQLite locale impossible. "
		"Vérifiez DATABASE_URL et les permissions d’écriture du dossier."
	)
	st.stop()


@st.fragment(run_every="1h")
def _check_daily_database_backup():
	git_backup.start_daily_backup_check()


_check_daily_database_backup()

if "connection_started_at" not in st.session_state:
	st.session_state["connection_started_at"] = datetime.datetime.now(datetime.UTC).replace(tzinfo=None).isoformat()
	# Ce journal est informatif : un échec d'écriture ne doit pas empêcher
	# l'utilisateur d'ouvrir le site.
	record_connections = os.getenv("STREAMLIT_RECORD_CONNECTIONS", "false").lower() in {
		"1", "true", "yes", "oui"
	}
	if not record_connections:
		st.session_state["connection_log_id"] = None
	else:
		try:
			st.session_state["connection_log_id"] = import_service.record_connection(
				st.session_state["connection_started_at"]
			)
		except Exception:
			st.session_state["connection_log_id"] = None

background_jobs.start_startup_updates_once(st.session_state.get("connection_log_id"))

if os.getenv("STREAMLIT_LIVE_PRIORITY_WATCHER", "true").strip().lower() in {
	"1", "true", "yes", "oui"
}:
	live_priority_watcher.start()

sidebar.render_app_rail("Tableau de bord")

ui.render_live_data_refresh()
dashboard.show()

with st.sidebar:
	st.caption(f"Connecté: {st.session_state.get('auth_user', 'utilisateur')}")
	auth.logout_button()
	st.markdown("---")
	st.markdown("### Mise à jour")
	st.caption(f"Connexion actuelle: {import_service.format_connection_label(st.session_state['connection_started_at'])}")
	ui.render_background_jobs()
	st.caption(f"Dernière MAJ en cours: {import_service.get_last_current_refresh_label()}")
	st.caption(f"Dernière MAJ historique: {import_service.get_last_auto_refresh_label()}")
	st.caption(import_service.get_api_access_message())
	cache_metrics_snapshot = None
	try:
		cache_metrics_snapshot = api_football.cache_metrics_snapshot()
		metrics_captions = api_metrics.format_api_metrics_captions(cache_metrics_snapshot)
	except Exception:
		# Sidebar telemetry must never take down the application. This also
		# contains KeyErrors from stale or incompatible runtime modules.
		logger.exception("API cache/quota metrics snapshot failed; using zero defaults")
		metrics_captions = (
			"Cache API · 0 requêtes data évitées (0 hits / 0 entrées)",
			"Quota · 0 préflights / 0 blocages · métriques locales au processus",
			"Quota partagé · 0 tokens réservés / 0 refus · 0 fallback(s) local(aux)",
		)
	for metrics_caption in metrics_captions:
		st.caption(metrics_caption)
	if not st.session_state.get("_api_metrics_runtime_logged"):
		logger.info(
			"API metrics runtime fingerprint: app=%s api_module=%s formatter=%s schema=%s snapshot_keys=%s",
			__file__, getattr(api_football, "__file__", "unknown"),
			getattr(api_metrics, "__file__", "unknown"),
			getattr(api_football, "CACHE_METRICS_SCHEMA_VERSION", "legacy"),
			sorted(cache_metrics_snapshot) if isinstance(cache_metrics_snapshot, dict) else [],
		)
		st.session_state["_api_metrics_runtime_logged"] = True
