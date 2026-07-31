import hmac
import os
import time
import uuid
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv


load_dotenv(Path(__file__).resolve().parents[1] / ".env")
LOGO_PATH = Path(__file__).resolve().parents[1] / "assets" / "prono-insight-logo.png"

AUTH_SESSION_KEY = "auth_session_id"
AUTH_ATTEMPTS_KEY = "auth_failed_attempts"
AUTH_LOCKED_UNTIL_KEY = "auth_locked_until"
LEGACY_AUTH_QUERY_PARAMS = ("prono_user", "prono_auth", "prono_expires")
MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_SECONDS = 60
UNSAFE_USERNAMES = {"admin"}
UNSAFE_PASSWORDS = {"admin", "change-moi", "changeme", "password"}


class AuthConfigurationError(RuntimeError):
    """Raised when authentication credentials are absent or unsafe."""


def _setting(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if value:
        return value
    try:
        return str(st.secrets.get(name, "") or "").strip()
    except Exception:
        return ""


def _credentials() -> tuple[str, str]:
    username = _setting("APP_USERNAME")
    password = _setting("APP_PASSWORD")
    if not username or not password:
        raise AuthConfigurationError(
            "APP_USERNAME et APP_PASSWORD doivent être configurés."
        )
    if username.casefold() in UNSAFE_USERNAMES or password.casefold() in UNSAFE_PASSWORDS:
        raise AuthConfigurationError(
            "Les identifiants d’exemple ou par défaut sont interdits."
        )
    return username, password


def _clear_legacy_auth_query() -> None:
    """Remove obsolete bearer tokens that older versions stored in the URL."""
    try:
        for key in LEGACY_AUTH_QUERY_PARAMS:
            if key in st.query_params:
                del st.query_params[key]
    except Exception:
        pass


def _start_auth_session(username: str) -> None:
    st.session_state.pop("logged_out", None)
    st.session_state.pop(AUTH_ATTEMPTS_KEY, None)
    st.session_state.pop(AUTH_LOCKED_UNTIL_KEY, None)
    st.session_state["authenticated"] = True
    st.session_state["auth_user"] = username
    st.session_state[AUTH_SESSION_KEY] = uuid.uuid4().hex


def _lockout_seconds_remaining() -> int:
    locked_until = float(st.session_state.get(AUTH_LOCKED_UNTIL_KEY, 0) or 0)
    return max(0, int(locked_until - time.monotonic()) + 1)


def _record_failed_attempt() -> int:
    attempts = int(st.session_state.get(AUTH_ATTEMPTS_KEY, 0) or 0) + 1
    st.session_state[AUTH_ATTEMPTS_KEY] = attempts
    if attempts >= MAX_LOGIN_ATTEMPTS:
        st.session_state[AUTH_LOCKED_UNTIL_KEY] = time.monotonic() + LOCKOUT_SECONDS
        st.session_state[AUTH_ATTEMPTS_KEY] = 0
    return attempts


def _clear_auth_state() -> None:
    _clear_legacy_auth_query()
    st.session_state.clear()
    st.session_state["logged_out"] = True


def is_authenticated() -> bool:
    _clear_legacy_auth_query()
    if bool(st.session_state.get("authenticated")) and not bool(st.session_state.get("logged_out")):
        return True
    return False


def handle_logout_query():
    return None


def logout_button():
    if st.sidebar.button("Déconnexion", width="stretch"):
        _clear_auth_state()
        st.rerun()


def login_page() -> bool:
    _clear_legacy_auth_query()
    try:
        expected_user, expected_password = _credentials()
    except AuthConfigurationError as exc:
        st.error(f"Configuration de connexion invalide : {exc}")
        st.caption(
            "Définissez des valeurs uniques dans .env ou dans les secrets Streamlit."
        )
        return False

    if LOGO_PATH.exists():
        left, logo_column, right = st.columns([1, 0.8, 1])
        with logo_column:
            st.image(str(LOGO_PATH), width="stretch")

    st.markdown("## Connexion")
    st.caption("Connectez-vous pour accéder au tableau de bord Prono insight.")

    with st.container(border=True):
        username = st.text_input("Identifiant", value="")
        password = st.text_input("Mot de passe", value="", type="password")
        remaining = _lockout_seconds_remaining()
        submitted = st.button(
            "Se connecter",
            type="primary",
            width="stretch",
            disabled=remaining > 0,
        )

    if remaining > 0:
        st.warning(
            f"Trop de tentatives. Réessayez dans {remaining} seconde(s)."
        )

    if submitted:
        clean_username = username.strip()
        clean_password = password
        valid_username = hmac.compare_digest(clean_username, str(expected_user))
        valid_password = hmac.compare_digest(clean_password, str(expected_password))
        if valid_username and valid_password:
            _start_auth_session(clean_username)
            st.rerun()
            return True
        _record_failed_attempt()
        st.error("Identifiant ou mot de passe incorrect.")

    return False
