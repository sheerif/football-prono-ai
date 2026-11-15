import streamlit as st
import datetime
import os

from services import background_jobs, git_backup, import_service, schema_guard
from components import auth, sidebar, ui
from pages import dashboard, data_management, api_widgets, matchs_a_venir, analyse_match, prediction_ia

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
