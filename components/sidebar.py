import streamlit as st

def render_sidebar():
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
        [
            "Tableau de bord",
            "Widgets Live",
            "Traitement des données",
            "Logs des MAJ",
            "Analyse match",
            "Comparaison équipes",
            "Prédiction IA",
            "Meilleurs pronostics",
        ],
        index=0,
    )
