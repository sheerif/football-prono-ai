import hashlib
import hmac
import os
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv


load_dotenv(Path(__file__).resolve().parents[1] / ".env")

AUTH_USER_PARAM = "prono_user"
AUTH_TOKEN_PARAM = "prono_auth"


def _credentials() -> tuple[str, str]:
    username = os.getenv("APP_USERNAME") or st.secrets.get("APP_USERNAME", "admin")
    password = os.getenv("APP_PASSWORD") or st.secrets.get("APP_PASSWORD", "admin")
    return username, password


def _auth_token(username: str, password: str) -> str:
    secret = os.getenv("APP_AUTH_SECRET") or str(password)
    return hmac.new(secret.encode("utf-8"), str(username).encode("utf-8"), hashlib.sha256).hexdigest()


def _query_value(name: str) -> str:
    try:
        value = st.query_params.get(name, "")
    except Exception:
        return ""
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value or "")


def _save_auth_query(username: str, password: str) -> None:
    try:
        _clear_auth_query()
        st.query_params.update(
            {
                AUTH_USER_PARAM: username,
                AUTH_TOKEN_PARAM: _auth_token(username, password),
            }
        )
    except Exception:
        pass


def _clear_auth_query() -> None:
    try:
        for key in [AUTH_USER_PARAM, AUTH_TOKEN_PARAM]:
            if key in st.query_params:
                del st.query_params[key]
    except Exception:
        pass


def _restore_auth_from_query() -> bool:
    expected_user, expected_password = _credentials()
    username = _query_value(AUTH_USER_PARAM)
    token = _query_value(AUTH_TOKEN_PARAM)
    expected_token = _auth_token(str(expected_user), str(expected_password))
    if username and token and hmac.compare_digest(username, str(expected_user)) and hmac.compare_digest(token, expected_token):
        st.session_state.pop("logged_out", None)
        st.session_state["authenticated"] = True
        st.session_state["auth_user"] = username
        return True
    return False


def _clear_auth_state() -> None:
    st.session_state["logged_out"] = True
    st.session_state.pop("authenticated", None)
    st.session_state.pop("auth_user", None)
    _clear_auth_query()


def is_authenticated() -> bool:
    if bool(st.session_state.get("authenticated")) and not bool(st.session_state.get("logged_out")):
        return True
    if bool(st.session_state.get("logged_out")):
        return False
    return _restore_auth_from_query()


def handle_logout_query():
    return None


def logout_button():
    if st.sidebar.button("Déconnexion", width="stretch"):
        _clear_auth_state()
        st.rerun()


def login_page() -> bool:
    expected_user, expected_password = _credentials()
    if _query_value(AUTH_USER_PARAM) or _query_value(AUTH_TOKEN_PARAM):
        _clear_auth_query()

    st.markdown("## Connexion")
    st.caption("Connectez-vous pour accéder au tableau de bord Prono insight.")

    with st.container(border=True):
        username = st.text_input("Identifiant", value="", placeholder="admin")
        password = st.text_input("Mot de passe", value="", type="password")
        submitted = st.button("Se connecter", type="primary", width="stretch")

    if submitted:
        clean_username = username.strip()
        clean_password = password.strip()
        valid_username = hmac.compare_digest(clean_username, str(expected_user))
        valid_password = hmac.compare_digest(clean_password, str(expected_password))
        if valid_username and valid_password:
            _clear_auth_query()
            st.session_state.pop("logged_out", None)
            st.session_state["authenticated"] = True
            st.session_state["auth_user"] = clean_username
            _save_auth_query(clean_username, str(expected_password))
            st.rerun()
            return True
        st.error("Identifiant ou mot de passe incorrect.")

    return False
