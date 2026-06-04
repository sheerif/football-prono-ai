import hmac
import os

import streamlit as st
from dotenv import load_dotenv


load_dotenv()


def _credentials() -> tuple[str, str]:
    username = os.getenv("APP_USERNAME", "admin")
    password = os.getenv("APP_PASSWORD", "admin")
    return username, password


def is_authenticated() -> bool:
    return bool(st.session_state.get("authenticated"))


def logout_button():
    if st.sidebar.button("Déconnexion", use_container_width=True):
        st.session_state["authenticated"] = False
        st.session_state.pop("auth_user", None)
        st.rerun()


def login_page() -> bool:
    expected_user, expected_password = _credentials()

    st.markdown("## Connexion")
    st.caption("Connectez-vous pour accéder au tableau de bord Football Prono AI.")

    with st.container(border=True):
        username = st.text_input("Identifiant", value="", placeholder="admin")
        password = st.text_input("Mot de passe", value="", type="password")
        submitted = st.button("Se connecter", type="primary", use_container_width=True)

    if submitted:
        valid_user = hmac.compare_digest(username, expected_user)
        valid_password = hmac.compare_digest(password, expected_password)
        if valid_user and valid_password:
            st.session_state["authenticated"] = True
            st.session_state["auth_user"] = username
            st.rerun()
        else:
            st.error("Identifiant ou mot de passe incorrect.")

    with st.expander("Configuration"):
        st.caption("Les identifiants sont lus depuis `.env`.")
        st.code("APP_USERNAME=admin\nAPP_PASSWORD=change-moi", language="bash")

    return False
