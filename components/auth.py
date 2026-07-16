import hashlib
import hmac
import os
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv


load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=True)

AUTH_USER_PARAM = "prono_user"
AUTH_TOKEN_PARAM = "prono_auth"
USERNAME_KEYS = ("APP_USERNAME", "AUTH_USERNAME", "USERNAME")
PASSWORD_KEYS = ("APP_PASSWORD", "AUTH_PASSWORD", "PASSWORD")


def _secret_value(name: str, fallback: str = "") -> str:
    try:
        return str(st.secrets.get(name, fallback))
    except Exception:
        return fallback


def _clean_credential(value) -> str:
    text_value = str(value or "").strip()
    if len(text_value) >= 2 and text_value[0] == text_value[-1] and text_value[0] in {"'", '"'}:
        text_value = text_value[1:-1].strip()
    return text_value


def _first_config_value(keys: tuple[str, ...], fallback: str) -> str:
    for key in keys:
        value = os.getenv(key)
        if _clean_credential(value):
            return _clean_credential(value)
    for key in keys:
        value = _secret_value(key)
        if _clean_credential(value):
            return _clean_credential(value)
    return fallback


def _credentials() -> tuple[str, str]:
    username = _first_config_value(USERNAME_KEYS, "admin")
    password = _first_config_value(PASSWORD_KEYS, "admin")
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
    if username or token:
        _clear_auth_query()
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

    st.markdown("## Connexion")
    st.caption("Connectez-vous pour accéder au tableau de bord Prono insight.")

    with st.container(border=True):
        username = st.text_input("Identifiant", value="", placeholder="admin")
        password = st.text_input("Mot de passe", value="", type="password")
        submitted = st.button("Se connecter", type="primary", width="stretch")

    if submitted:
        clean_username = username.strip()
        clean_password = password.strip()
        valid_username = hmac.compare_digest(clean_username.lower(), expected_user.lower())
        valid_password = hmac.compare_digest(clean_password, expected_password)
        if valid_username and valid_password:
            st.session_state.pop("logged_out", None)
            st.session_state["authenticated"] = True
            st.session_state["auth_user"] = expected_user
            _save_auth_query(expected_user, expected_password)
            st.rerun()
            return True
        else:
            st.error("Identifiant ou mot de passe incorrect.")
            st.caption(f"Identifiant configuré sur le serveur : `{expected_user}`")

    return False
