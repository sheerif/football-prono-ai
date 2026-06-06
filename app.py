import streamlit as st
import datetime
import os

os.environ["AUTO_REFRESH_END_SEASON"] = "2026"

from services import import_service, schema_guard


def _force_2026_active_season(session, league_id: int, fallback_season: int) -> int:
	return max(2026, int(fallback_season or 2026))


import_service._active_season_for_league = _force_2026_active_season

from components import auth, sidebar, ui
from pages import dashboard, data_management, update_logs, api_widgets, analyse_match, comparaison_equipes, prediction_ia, top_pronostics

st.set_page_config(page_title="Football Prono AI", layout="wide", initial_sidebar_state="expanded")
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

sidebar.render_app_rail("Tableau de bord")

dashboard.show()

with st.sidebar:
	st.caption(f"Connecté: {st.session_state.get('auth_user', 'utilisateur')}")
	auth.logout_button()
	st.markdown("---")
	st.markdown("### Mise à jour")
	st.caption(f"Connexion actuelle: {import_service.format_connection_label(st.session_state['connection_started_at'])}")
	with st.spinner("Vérification des données..."):
		if "current_competitions_refreshed" not in st.session_state:
			current_started_at = datetime.datetime.now(datetime.UTC).replace(tzinfo=None).isoformat()
			current_sync_result = import_service.refresh_current_competitions_on_connection()
			import_service.record_update_result("championnats_en_cours", current_started_at, current_sync_result)
			if current_sync_result["ran"] and st.session_state.get("connection_log_id"):
				import_service.mark_connection_current_refreshed(st.session_state["connection_log_id"])
			st.session_state["current_competitions_refreshed"] = True
		else:
			current_sync_result = {"ran": False, "reason": "Championnats en cours déjà vérifiés pour cette connexion."}
		if "auto_refresh_checked" not in st.session_state:
			auto_started_at = datetime.datetime.now(datetime.UTC).replace(tzinfo=None).isoformat()
			sync_result = import_service.auto_refresh_if_due()
			import_service.record_update_result("historique_auto", auto_started_at, sync_result)
			st.session_state["auto_refresh_checked"] = True
			st.session_state["auto_refresh_result"] = sync_result
		else:
			sync_result = st.session_state["auto_refresh_result"]
	if current_sync_result["ran"]:
		st.success("Championnats en cours mis à jour.")
	else:
		st.caption(current_sync_result["reason"])
	if sync_result["ran"]:
		st.success("Historique mis à jour depuis l’API.")
	else:
		st.caption(sync_result["reason"])
	st.caption(f"Dernière MAJ en cours: {import_service.get_last_current_refresh_label()}")
	st.caption(f"Dernière MAJ historique: {import_service.get_last_auto_refresh_label()}")
	st.caption(import_service.get_api_access_message())
