import streamlit as st

NAV_ITEMS = [
    "Tableau de bord",
    "Widgets Live",
    "Traitement des données",
    "Logs des MAJ",
    "Analyse match",
    "Comparaison équipes",
    "Prédiction IA",
    "Meilleurs pronostics",
]

NAV_TARGETS = {
    "Tableau de bord": "./",
    "Widgets Live": "./api_widgets",
    "Traitement des données": "./data_management",
    "Logs des MAJ": "./update_logs",
    "Analyse match": "./analyse_match",
    "Comparaison équipes": "./comparaison_equipes",
    "Prédiction IA": "./prediction_ia",
    "Meilleurs pronostics": "./top_pronostics",
}


def render_sidebar(current: str = "Tableau de bord"):
    try:
        current_index = NAV_ITEMS.index(current)
    except ValueError:
        current_index = 0

    st.sidebar.markdown(
        """
        <div class="sidebar-brand">
            <div class="sidebar-mark">FP</div>
            <div>
                <h2>Football Prono AI</h2>
                <p>Analyse, signaux et prédictions</p>
            </div>
        </div>
        <style>
        .sidebar-brand {
            display: flex;
            align-items: center;
            gap: 0.75rem;
            padding: 0.85rem;
            margin: 0.1rem 0 0.85rem 0;
            border-radius: 8px;
            background:
                linear-gradient(135deg, rgba(20, 37, 29, 0.98), rgba(32, 54, 43, 0.94)),
                repeating-linear-gradient(90deg, rgba(255, 255, 255, 0.07) 0 1px, transparent 1px 42px);
            box-shadow: 0 14px 28px rgba(22, 32, 27, 0.14);
        }
        .sidebar-mark {
            display: grid;
            place-items: center;
            width: 2.55rem;
            height: 2.55rem;
            border-radius: 8px;
            background: linear-gradient(135deg, #b9d76f, #d8a528);
            color: #14251d;
            font-size: 0.88rem;
            font-weight: 900;
            box-shadow: 0 12px 24px rgba(0, 0, 0, 0.20);
        }
        .sidebar-brand h2 {
            margin: 0;
            font-size: 1.02rem;
            line-height: 1.1;
            color: #ffffff;
        }
        .sidebar-brand p {
            margin: 0.2rem 0 0 0;
            font-size: 0.78rem;
            color: rgba(255, 255, 255, 0.72);
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.sidebar.markdown("---")
    return st.sidebar.radio(
        "Navigation",
        NAV_ITEMS,
        index=current_index,
    )


def switch_to_nav(nav: str):
    page_targets = {
        "Tableau de bord": "app.py",
        "Widgets Live": "pages/api_widgets.py",
        "Traitement des données": "pages/data_management.py",
        "Logs des MAJ": "pages/update_logs.py",
        "Analyse match": "pages/analyse_match.py",
        "Comparaison équipes": "pages/comparaison_equipes.py",
        "Prédiction IA": "pages/prediction_ia.py",
        "Meilleurs pronostics": "pages/top_pronostics.py",
    }
    target = page_targets.get(nav)
    if target:
        st.switch_page(target)


def render_app_rail(current: str):
    links = []
    for item in NAV_ITEMS:
        active = " active" if item == current else ""
        links.append(f'<a class="app-rail-link{active}" href="{NAV_TARGETS[item]}" target="_self">{item}</a>')

    st.markdown(
        f"""
        <style>
        [data-testid="stSidebar"] {{
            display: none !important;
        }}
        .block-container {{
            max-width: none !important;
            padding-left: 320px !important;
            padding-right: 2rem !important;
        }}
        .app-rail {{
            position: fixed;
            inset: 0 auto 0 0;
            z-index: 999990;
            width: 288px;
            padding: 1.2rem 1rem;
            overflow-y: auto;
            background: #f4f7ee;
            border-right: 1px solid rgba(22, 32, 27, 0.10);
        }}
        .app-rail-brand {{
            display: flex;
            align-items: center;
            gap: 0.75rem;
            padding: 0.85rem;
            margin-bottom: 0.9rem;
            border-radius: 8px;
            background:
                linear-gradient(135deg, rgba(20, 37, 29, 0.98), rgba(32, 54, 43, 0.94)),
                repeating-linear-gradient(90deg, rgba(255, 255, 255, 0.07) 0 1px, transparent 1px 42px);
            box-shadow: 0 14px 28px rgba(22, 32, 27, 0.14);
        }}
        .app-rail-mark {{
            display: grid;
            place-items: center;
            flex: 0 0 auto;
            width: 2.55rem;
            height: 2.55rem;
            border-radius: 8px;
            background: linear-gradient(135deg, #b9d76f, #d8a528);
            color: #14251d;
            font-size: 0.88rem;
            font-weight: 900;
        }}
        .app-rail-title {{
            color: #ffffff;
            font-size: 1.02rem;
            line-height: 1.1;
            font-weight: 900;
        }}
        .app-rail-subtitle {{
            margin-top: 0.2rem;
            color: rgba(255, 255, 255, 0.72);
            font-size: 0.78rem;
            line-height: 1.2;
        }}
        .app-rail-nav {{
            display: grid;
            gap: 0.28rem;
            padding-top: 0.4rem;
            border-top: 1px solid rgba(22, 32, 27, 0.12);
        }}
        .app-rail-footer {{
            margin-top: 1rem;
            padding-top: 0.8rem;
            border-top: 1px solid rgba(22, 32, 27, 0.12);
        }}
        .app-rail-link {{
            display: block;
            padding: 0.58rem 0.7rem;
            border-radius: 8px;
            color: #16201b !important;
            text-decoration: none !important;
            font-size: 0.94rem;
            font-weight: 750;
        }}
        .app-rail-link:hover {{
            background: rgba(18, 100, 71, 0.08);
        }}
        .app-rail-link.active {{
            color: #126447 !important;
            background: rgba(18, 100, 71, 0.12);
            box-shadow: inset 3px 0 0 #126447;
        }}
        .app-rail-logout {{
            color: #8f2d25 !important;
        }}
        .app-rail-logout:hover {{
            background: rgba(201, 75, 63, 0.10);
        }}
        @media (max-width: 760px) {{
            .block-container {{
                padding-top: 5.25rem;
                padding-left: 1rem !important;
                padding-right: 1rem !important;
            }}
            .app-rail {{
                right: 0;
                bottom: auto;
                width: auto;
                height: 4.4rem;
                padding: 0.55rem 0.75rem;
                border-right: 0;
                border-bottom: 1px solid rgba(22, 32, 27, 0.10);
            }}
            .app-rail-brand {{
                display: none;
            }}
            .app-rail-nav {{
                display: flex;
                gap: 0.35rem;
                overflow-x: auto;
                border-top: 0;
                padding-top: 0;
            }}
            .app-rail-link {{
                flex: 0 0 auto;
                white-space: nowrap;
            }}
        }}
        </style>
        <nav class="app-rail" aria-label="Navigation principale">
            <div class="app-rail-brand">
                <div class="app-rail-mark">FP</div>
                <div>
                    <div class="app-rail-title">Football Prono AI</div>
                    <div class="app-rail-subtitle">Analyse, signaux et prédictions</div>
                </div>
            </div>
            <div class="app-rail-nav">
                {"".join(links)}
            </div>
            <div class="app-rail-footer">
                <a class="app-rail-link app-rail-logout" href="./?logout=1" target="_self">Déconnexion</a>
            </div>
        </nav>
        """,
        unsafe_allow_html=True,
    )
