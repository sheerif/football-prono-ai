import streamlit as st
import datetime
import os

os.environ["AUTO_REFRESH_END_SEASON"] = "2026"

from services import background_jobs, import_service, schema_guard


def _force_2026_active_season(session, league_id: int, fallback_season: int) -> int:
	return max(2026, int(fallback_season or 2026))


import_service._active_season_for_league = _force_2026_active_season

from components import auth, sidebar, ui
from pages import dashboard, data_management, api_widgets, matchs_a_venir, analyse_match, comparaison_equipes, prediction_ia, top_pronostics

st.set_page_config(page_title="Prono insight", layout="wide", initial_sidebar_state="expanded")
ui.inject_app_style()

if not auth.is_authenticated():
	auth.login_page()
	st.stop()

@st.cache_resource(show_spinner=False)
def _init_db_once():
	import_service.init_db()


_init_db_once()
schema_guard.ensure_match_score_columns()

if "connection_started_at" not in st.session_state:
	st.session_state["connection_started_at"] = datetime.datetime.now(datetime.UTC).replace(tzinfo=None).isoformat()
	st.session_state["connection_log_id"] = import_service.record_connection(st.session_state["connection_started_at"])

background_jobs.start_startup_updates_once(st.session_state.get("connection_log_id"))

sidebar.render_app_rail("Tableau de bord")

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
