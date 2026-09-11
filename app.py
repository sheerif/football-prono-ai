import streamlit as st
import datetime
import os

from services import background_jobs, import_service, schema_guard
from database.database import (
	persistence_configuration_error,
	persistence_mode,
	start_realtime_replica_sync,
)

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
	start_realtime_replica_sync()
except Exception:
	mode = persistence_mode()
	config_error = persistence_configuration_error()
	if mode == "turso" or config_error:
		st.error(
			"Connexion à la base Turso impossible. Aucune mise à jour API n’a été "
			"lancée. Vérifiez TURSO_DATABASE_URL et TURSO_AUTH_TOKEN dans les "
			"secrets Streamlit, puis redémarrez l’application."
		)
	else:
		st.error(
			"Initialisation de la base impossible. Aucune mise à jour API n’a été lancée."
		)
	st.stop()

if "connection_started_at" not in st.session_state:
	st.session_state["connection_started_at"] = datetime.datetime.now(datetime.UTC).replace(tzinfo=None).isoformat()
	st.session_state["connection_log_id"] = import_service.record_connection(st.session_state["connection_started_at"])

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
