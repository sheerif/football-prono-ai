import hmac
import os
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv


load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def _credentials() -> tuple[str, str]:
    username = os.getenv("APP_USERNAME", "admin")
    password = os.getenv("APP_PASSWORD", "admin")
    return username, password


def is_authenticated() -> bool:
    try:
        logout_requested = st.query_params.get("logout") == "1"
    except Exception:
        logout_requested = False
    if logout_requested:
        st.session_state["logged_out"] = True
        st.session_state.pop("authenticated", None)
        st.session_state.pop("auth_user", None)
        try:
            st.query_params.clear()
        except Exception:
            pass
    return not bool(st.session_state.get("logged_out"))


def handle_logout_query():
    try:
        logout_requested = st.query_params.get("logout") == "1"
    except Exception:
        logout_requested = False
    if logout_requested:
        st.session_state["logged_out"] = True
        st.session_state.pop("authenticated", None)
        st.session_state.pop("auth_user", None)
        try:
            st.query_params.clear()
        except Exception:
            pass
        st.rerun()


def logout_button():
    if st.sidebar.button("Déconnexion", width="stretch"):
        st.session_state["logged_out"] = True
        st.session_state.pop("authenticated", None)
        st.session_state.pop("auth_user", None)
        st.rerun()


def login_page() -> bool:
    expected_user, expected_password = _credentials()

    st.markdown("## Connexion")
    st.caption("Connectez-vous pour accéder au tableau de bord Football Prono AI.")

    with st.container(border=True):
        username = st.text_input("Identifiant", value="", placeholder="admin")
        password = st.text_input("Mot de passe", value="", type="password")
        submitted = st.button("Se connecter", type="primary", width="stretch")

    if submitted:
        clean_username = username.strip()
        st.session_state.pop("logged_out", None)
        st.session_state["authenticated"] = True
        st.session_state["auth_user"] = clean_username or "admin"
        st.rerun()

    with st.expander("Configuration"):
        st.caption("Les identifiants sont lus depuis `.env`.")
        st.code("APP_USERNAME=admin\nAPP_PASSWORD=change-moi", language="bash")

    return False
