from pathlib import Path

import streamlit as st

LOGO_PATH = Path(__file__).resolve().parents[1] / "assets" / "prono-insight-logo.png"


def _render_logo(width: int = 148):
    if not LOGO_PATH.exists():
        return
    left, center, right = st.columns([1, 2.2, 1])
    with center:
        st.image(str(LOGO_PATH), width=width)


NAV_ITEMS = [
    "Tableau de bord",
    "Widgets Live",
    "Mise à jour",
    "Joueurs",
    "Équipes",
    "Matchs à venir",
    "Analyse & comparaison",
    "Prédictions",
]

NAV_TARGETS = {
    "Tableau de bord": "./",
    "Widgets Live": "./api_widgets",
    "Mise à jour": "./data_management",
    "Joueurs": "./joueurs",
    "Équipes": "./progression",
    "Matchs à venir": "./matchs_a_venir",
    "Analyse & comparaison": "./analyse_match",
    "Prédictions": "./prediction_ia",
}

NAV_ICONS = {
    "Tableau de bord": "⌂",
    "Widgets Live": "◉",
    "Mise à jour": "↻",
    "Joueurs": "♙",
    "Équipes": "↗",
    "Matchs à venir": "◷",
    "Analyse & comparaison": "◈",
    "Prédictions": "✦",
}


def render_sidebar(current: str = "Tableau de bord"):
    """Compatibility wrapper for the single application navigation.

    Older pages imported ``render_sidebar`` directly.  Keeping this thin
    wrapper avoids rendering a second, visually different menu while routing
    every caller through the accessible ``render_app_rail`` implementation.
    """
    return render_app_rail(current)


def switch_to_nav(nav: str):
    page_targets = {
        "Tableau de bord": "app.py",
        "Widgets Live": "pages/api_widgets.py",
        "Mise à jour": "pages/data_management.py",
        "Joueurs": "pages/joueurs.py",
        "Équipes": "pages/progression.py",
        "Matchs à venir": "pages/matchs_a_venir.py",
        "Analyse & comparaison": "pages/analyse_match.py",
        "Prédictions": "pages/prediction_ia.py",
    }
    target = page_targets.get(nav)
    if target:
        st.switch_page(target)


def render_app_rail(current: str):
    st.markdown(
        """
        <style>
        [data-testid="stSidebar"] {
            background:
                radial-gradient(circle at 50% 0%, rgba(42,161,152,.12), transparent 17rem),
                linear-gradient(180deg, #071a2b 0%, #0a243b 100%);
            border-right: 1px solid rgba(42, 161, 152, 0.28);
        }
        [data-testid="stSidebar"] .stButton > button {
            justify-content: flex-start;
            width: 100%;
            min-height: 2.45rem;
            border: 0;
            border-radius: 8px;
            background: transparent;
            color: #dce5ed;
            font-weight: 750;
        }
        .app-rail-label {
            margin: .35rem .2rem .45rem;
            color: rgba(220,229,237,.52);
            font-size: .66rem;
            letter-spacing: .14em;
            font-weight: 800;
        }
        .app-rail-nav-icon {
            display: inline-grid;
            place-items: center;
            width: 1.35rem;
            margin-right: .35rem;
            color: #72d6c8;
            font-size: 1.05rem;
        }
        [data-testid="stSidebar"] .stButton > button:hover {
            background: rgba(42, 161, 152, 0.10);
            color: #72d6c8;
        }
        [data-testid="stSidebar"] .stButton > button:focus-visible {
            outline: 3px solid #72d6c8;
            outline-offset: 2px;
            box-shadow: 0 0 0 4px rgba(114, 214, 200, .28);
        }
        .app-rail-brand {
            display: flex;
            align-items: center;
            gap: 0.75rem;
            padding: 0.85rem;
            margin-bottom: 0.9rem;
            border: 1px solid rgba(42,161,152,.30);
            border-radius: 12px;
            background:
                linear-gradient(135deg, rgba(11, 43, 72, 0.98), rgba(7, 26, 43, 0.94)),
                repeating-linear-gradient(90deg, rgba(255, 255, 255, 0.07) 0 1px, transparent 1px 42px);
            box-shadow: 0 14px 28px rgba(0, 0, 0, 0.24);
        }
        .app-rail-mark {
            display: grid;
            place-items: center;
            flex: 0 0 auto;
            width: 2.55rem;
            height: 2.55rem;
            border-radius: 8px;
            background: linear-gradient(135deg, #72d6c8, #2aa198);
            color: #071a2b;
            font-size: 0.88rem;
            font-weight: 900;
        }
        .app-rail-title {
            color: #ffffff;
            font-size: 1.02rem;
            line-height: 1.1;
            font-weight: 900;
        }
        .app-rail-subtitle {
            margin-top: 0.2rem;
            color: rgba(255, 255, 255, 0.72);
            font-size: 0.78rem;
            line-height: 1.2;
        }
        .app-rail-current {
            display: block;
            padding: 0.58rem 0.7rem;
            margin: 0.15rem 0;
            border-radius: 8px;
            color: #9be5dc;
            background: linear-gradient(90deg, rgba(42,161,152,.18), rgba(42,161,152,.05));
            box-shadow: inset 3px 0 0 #2aa198;
            font-size: 0.94rem;
            font-weight: 750;
        }
        /* Mobile */
        @media (max-width: 600px) {
            [data-testid="stSidebar"] .stButton > button {
                min-height: 2.18rem;
                padding: 0.32rem 0.5rem;
                font-size: 0.86rem;
            }
            .app-rail-current {
                padding: 0.42rem 0.55rem;
                font-size: 0.86rem;
            }
            .app-rail-brand {
                gap: 0.5rem;
                margin-bottom: 0.45rem;
            }
        }
        /* Tablet */
        @media (min-width: 601px) and (max-width: 900px) {
            [data-testid="stSidebar"] .stButton > button {
                min-height: 2.25rem;
                padding: 0.35rem 0.55rem;
                font-size: 0.9rem;
            }
            .app-rail-current {
                padding: 0.48rem 0.6rem;
                font-size: 0.9rem;
            }
            .app-rail-brand {
                gap: 0.55rem;
                margin-bottom: 0.5rem;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    with st.sidebar:
        _render_logo()
        st.markdown(
            """
            <div class="app-rail-brand">
                <div>
                    <div class="app-rail-title">Prono insight</div>
                    <div class="app-rail-subtitle">Analyse, signaux et prédictions</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.markdown("---")
        st.markdown(
            '<div class="app-rail-label" role="navigation" aria-label="Navigation principale">NAVIGATION</div>',
            unsafe_allow_html=True,
        )
        for item in NAV_ITEMS:
            display_item = f"{NAV_ICONS.get(item, '•')}  {item}"
            if item == current:
                st.markdown(
                    f'<div class="app-rail-current" role="link" tabindex="0" aria-current="page" aria-label="Page actuelle : {item}">'
                    f'<span class="app-rail-nav-icon" aria-hidden="true">{NAV_ICONS.get(item, "•")}</span>{item}</div>',
                    unsafe_allow_html=True,
                )
            elif st.button(display_item, key=f"nav_{item}", width="stretch"):
                switch_to_nav(item)
