import streamlit as st

NAV_ITEMS = [
    "Tableau de bord",
    "Widgets Live",
    "Mise à jour",
    "Matchs à venir",
    "Analyse match",
    "Comparaison équipes",
    "Prédiction IA",
    "Meilleurs pronostics",
]

NAV_TARGETS = {
    "Tableau de bord": "./",
    "Widgets Live": "./api_widgets",
    "Mise à jour": "./data_management",
    "Matchs à venir": "./matchs_a_venir",
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
            <div class="sidebar-mark">PI</div>
            <div>
                <h2>Prono insight</h2>
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

    # Custom navigation buttons (accessible + styled)
    selected = current

    # Small CSS to style the buttons and active state consistently
    st.sidebar.markdown(
        """
        <style>
        [data-testid="stSidebar"] .stButton > button {
            justify-content: center;
            width: 100%;
            min-height: 2.6rem;
            border: 0;
            border-radius: 10px;
            background: linear-gradient(180deg, rgba(255,255,255,0.96), rgba(250,252,247,0.92));
            color: #16201b;
            font-weight: 700;
            margin: 0.45rem 0;
            box-shadow: 0 10px 24px rgba(18, 100, 71, 0.06);
        }
        [data-testid="stSidebar"] .stButton > button:hover {
            transform: translateY(-1px);
        }
        .app-rail-current {
            display: block;
            padding: 0.58rem 0.7rem;
            margin: 0.15rem 0;
            border-radius: 10px;
            color: #126447;
            background: rgba(18, 100, 71, 0.10);
            box-shadow: inset 4px 0 0 #126447;
            font-size: 0.95rem;
            font-weight: 800;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    with st.sidebar:
        # Render buttons and current item
        for item in NAV_ITEMS:
            if item == current:
                st.markdown(f'<div class="app-rail-current">{item}</div>', unsafe_allow_html=True)
            else:
                if st.button(item, key=f"nav_{item}"):
                    selected = item

        return selected


def switch_to_nav(nav: str):
    page_targets = {
        "Tableau de bord": "app.py",
        "Widgets Live": "pages/api_widgets.py",
        "Mise à jour": "pages/data_management.py",
        "Matchs à venir": "pages/matchs_a_venir.py",
        "Analyse match": "pages/analyse_match.py",
        "Comparaison équipes": "pages/comparaison_equipes.py",
        "Prédiction IA": "pages/prediction_ia.py",
        "Meilleurs pronostics": "pages/top_pronostics.py",
    }
    target = page_targets.get(nav)
    if target:
        st.switch_page(target)


def render_app_rail(current: str):
    st.markdown(
        """
        <style>
        [data-testid="stSidebar"] {
            background: #f4f7ee;
            border-right: 1px solid rgba(22, 32, 27, 0.10);
        }
        [data-testid="stSidebar"] .stButton > button {
            justify-content: flex-start;
            width: 100%;
            min-height: 2.45rem;
            border: 0;
            border-radius: 8px;
            background: transparent;
            color: #16201b;
            font-weight: 750;
        }
        [data-testid="stSidebar"] .stButton > button:hover {
            background: rgba(18, 100, 71, 0.08);
            color: #126447;
        }
        .app-rail-brand {
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
        }
        .app-rail-mark {
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
            color: #126447;
            background: rgba(18, 100, 71, 0.12);
            box-shadow: inset 3px 0 0 #126447;
            font-size: 0.94rem;
            font-weight: 750;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.markdown(
            """
            <div class="app-rail-brand">
                <div class="app-rail-mark">PI</div>
                <div>
                    <div class="app-rail-title">Prono insight</div>
                    <div class="app-rail-subtitle">Analyse, signaux et prédictions</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.markdown("---")
        for item in NAV_ITEMS:
            if item == current:
                st.markdown(f'<div class="app-rail-current">{item}</div>', unsafe_allow_html=True)
            elif st.button(item, key=f"nav_{item}", width="stretch"):
                switch_to_nav(item)
