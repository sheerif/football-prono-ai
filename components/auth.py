import hmac
import os
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv


load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def _credentials() -> tuple[str, str]:
    username = os.getenv("APP_USERNAME") or st.secrets.get("APP_USERNAME", "admin")
    password = os.getenv("APP_PASSWORD") or st.secrets.get("APP_PASSWORD", "admin")
    return username, password


def _clear_auth_state() -> None:
    st.session_state["logged_out"] = True
    st.session_state.pop("authenticated", None)
    st.session_state.pop("auth_user", None)


def is_authenticated() -> bool:
    return bool(st.session_state.get("authenticated")) and not bool(st.session_state.get("logged_out"))


def handle_logout_query():
    return None


def logout_button():
    if st.sidebar.button("Déconnexion", width="stretch"):
        _clear_auth_state()
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
        valid_username = hmac.compare_digest(clean_username, str(expected_user))
        valid_password = hmac.compare_digest(password, str(expected_password))
        if valid_username and valid_password:
            st.session_state.pop("logged_out", None)
            st.session_state["authenticated"] = True
            st.session_state["auth_user"] = clean_username
            st.rerun()
        st.error("Identifiant ou mot de passe incorrect.")

    with st.expander("Configuration"):
        st.caption("Les identifiants sont lus depuis `.env`.")
        st.code(f"APP_USERNAME={expected_user}\nAPP_PASSWORD={expected_password}", language="bash")

    return False
