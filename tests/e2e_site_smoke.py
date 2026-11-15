"""End-to-end smoke test for every user-facing Streamlit page.

The app runs against a temporary SQLite database and dummy credentials.
No API-Football key is supplied, so the test does not consume external API quota.
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
PAGES = (
    "Tableau de bord",
    "Widgets Live",
    "Mise à jour",
    "Joueurs",
    "Équipes",
    "Matchs à venir",
    "xG",
    "Rapports PDF",
    "Analyse & comparaison",
    "Prédictions",
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_server(url: str, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 90
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"Streamlit s'est arrêté prématurément (code {process.returncode})."
            )
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
        time.sleep(1)
    raise RuntimeError(f"Streamlit n'a pas démarré à temps : {last_error}")


def _assert_no_streamlit_exception(page, current_page: str) -> None:
    exception = page.locator('[data-testid="stException"]')
    if exception.count():
        details = exception.first.inner_text(timeout=2)
        raise AssertionError(f"Erreur Streamlit sur « {current_page} » : {details}")


def main() -> int:
    os.chdir(ROOT)
    os.environ.setdefault("APP_USERNAME", "ci-test-user")
    os.environ.setdefault("APP_PASSWORD", "ci-test-password-not-used-in-production")
    os.environ["STREAMLIT_STARTUP_UPDATES"] = "false"
    os.environ["AUTO_REFRESH_ON_CONNECTION"] = "false"
    os.environ["AUTO_REFRESH_CURRENT_ON_CONNECTION"] = "false"
    os.environ["STREAMLIT_RECORD_CONNECTIONS"] = "false"
    os.environ["API_FOOTBALL_KEY"] = ""
    os.environ["API_FOOTBALL_WIDGET_KEY"] = ""

    with tempfile.TemporaryDirectory(prefix="prono-insight-e2e-") as temp_dir:
        os.environ["DATABASE_URL"] = f"sqlite:///{Path(temp_dir) / 'e2e.db'}"
        port = _free_port()
        command = [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            "app.py",
            "--server.headless=true",
            f"--server.port={port}",
            "--server.address=127.0.0.1",
            "--browser.gatherUsageStats=false",
        ]
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            env=os.environ.copy(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        try:
            _wait_for_server(f"http://127.0.0.1:{port}/_stcore/health", process)

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page(viewport={"width": 1365, "height": 900})
                page.set_default_timeout(15_000)
                page.goto(f"http://127.0.0.1:{port}", wait_until="domcontentloaded")

                page.get_by_label("Identifiant").fill(os.environ["APP_USERNAME"])
                page.get_by_label("Mot de passe").fill(os.environ["APP_PASSWORD"])
                page.get_by_role("button", name="Se connecter").click()
                page.locator(".app-rail-current").wait_for(state="visible")
                _assert_no_streamlit_exception(page, "Connexion / tableau de bord")
                print("PASS — Connexion et tableau de bord", flush=True)

                for page_name in PAGES[1:]:
                    nav_button = page.get_by_role(
                        "button", name=re.compile(re.escape(page_name))
                    )
                    nav_button.click()
                    page.locator(".app-rail-current").filter(has_text=page_name).wait_for(
                        state="visible"
                    )
                    page.wait_for_timeout(700)
                    _assert_no_streamlit_exception(page, page_name)
                    print(f"PASS — {page_name}", flush=True)

                page.set_viewport_size({"width": 390, "height": 844})
                page.wait_for_timeout(700)
                _assert_no_streamlit_exception(page, "Affichage mobile — Prédictions")

                viewport = page.evaluate(
                    "() => ({width: window.innerWidth, documentWidth: document.documentElement.scrollWidth})"
                )
                if viewport["documentWidth"] > viewport["width"] + 1:
                    raise AssertionError(
                        "Défilement horizontal global sur mobile : "
                        f"{viewport['documentWidth']} px pour un écran de {viewport['width']} px."
                    )

                nav_buttons = page.locator(
                    '[data-testid="stSidebar"] .stButton > button'
                )
                if nav_buttons.count():
                    min_height = nav_buttons.first.evaluate(
                        "(element) => element.getBoundingClientRect().height"
                    )
                    if min_height < 40:
                        raise AssertionError(
                            f"Zone tactile trop petite dans le menu mobile : {min_height:.0f} px."
                        )

                print("PASS — Affichage mobile sans débordement horizontal (390 × 844)", flush=True)
                print("PASS — Zones tactiles du menu mobile", flush=True)
                browser.close()
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
