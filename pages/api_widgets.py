import html
import os

import streamlit as st
from dotenv import load_dotenv

from components import ui


load_dotenv()


def _widget_html(api_key: str) -> str:
    safe_key = html.escape(api_key, quote=True)
    return f"""
    <!doctype html>
    <html lang="fr">
      <head>
        <meta charset="utf-8" />
        <script type="module" src="https://widgets.api-sports.io/3.1.0/widgets.js"></script>
        <style>
          html,
          body {{
            margin: 0;
            padding: 0;
            background: #f5f7f2;
            font-family: Inter, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          }}
          body {{
            display: flex;
            justify-content: center;
          }}
          .widget-wrap {{
            width: 100%;
            max-width: 760px;
            min-width: 0;
            height: 860px;
            overflow: hidden;
            background: #f5f7f2;
          }}
          /* API-Sports injects a wide table on desktop. Keep it inside the
             iframe on phones instead of forcing horizontal page scrolling. */
          api-sports-widget {{
            display: block;
            max-width: 100%;
            overflow-x: auto;
          }}
          @media (max-width: 600px) {{
            .widget-wrap {{ height: 760px; }}
            body {{ width: 100%; overflow-x: hidden; }}
          }}
        </style>
      </head>
      <body>
        <div class="widget-wrap">
          <api-sports-widget
            data-type="leagues"
            data-target-league="modal"
            data-show-logos="true"
            data-show-favorites="false">
          </api-sports-widget>
          <api-sports-widget
            data-type="config"
            data-key="{safe_key}"
            data-sport="football"
            data-lang="en"
            data-theme="grey"
            data-timezone="Europe/Paris"
            data-show-errors="true">
          </api-sports-widget>
        </div>
      </body>
    </html>
    """


def show():
    ui.page_hero(
        "Widgets Live",
        "Liste officielle des ligues et pays fournie par API-Sports.",
    )

    api_key = os.getenv("API_FOOTBALL_KEY", "")
    if not api_key:
        st.error("API_FOOTBALL_KEY est absent du fichier .env.")
        return

    # ``width=780`` caused a horizontal overflow on phones.  Stretch lets
    # Streamlit size the iframe to the available content width.
    st.iframe(_widget_html(api_key), height=880, width="stretch")


if __name__ == "__main__":
    ui.run_direct_page("Widgets Live", show)
