import streamlit as st

from services.season_format import season_period


def inject_app_style():
    st.markdown(
        """
        <style>
        :root {
            --app-bg: #f5f7f2;
            --app-surface: #ffffff;
            --app-ink: #16201b;
            --app-muted: #66736b;
            --app-line: rgba(22, 32, 27, 0.10);
            --app-green: #126447;
            --app-lime: #b9d76f;
            --app-gold: #d8a528;
            --app-red: #c94b3f;
        }
        html, body, [data-testid="stAppViewContainer"] {
            background:
                radial-gradient(circle at 18% 0%, rgba(185, 215, 111, 0.16), transparent 28rem),
                linear-gradient(180deg, #fbfcf8 0%, var(--app-bg) 100%);
            color: var(--app-ink);
        }
        .block-container {
            max-width: 1420px;
            padding-top: 1.35rem;
            padding-bottom: 3rem;
            padding-left: 1.5rem;
            padding-right: 1.5rem;
        }
        h1, h2, h3 {
            color: var(--app-ink);
            letter-spacing: 0;
            overflow-wrap: anywhere;
        }
        p, span, label, div {
            overflow-wrap: anywhere;
        }
        .page-header {
            display: flex;
            align-items: flex-end;
            justify-content: space-between;
            gap: 1.5rem;
            margin: 0.25rem 0 1.35rem 0;
            padding-bottom: 1rem;
            border-bottom: 1px solid rgba(22, 32, 27, 0.10);
        }
        .page-eyebrow {
            display: inline-flex;
            align-items: center;
            gap: 0.45rem;
            color: var(--app-green);
            font-size: 0.76rem;
            font-weight: 900;
            letter-spacing: 0.08em;
            text-transform: uppercase;
        }
        .page-eyebrow::before {
            content: "";
            width: 0.55rem;
            height: 0.55rem;
            border-radius: 50%;
            background: var(--app-gold);
            box-shadow: 0 0 0 4px rgba(216, 165, 40, 0.14);
        }
        .page-header h1 {
            margin: 0.35rem 0 0 0;
            color: var(--app-ink);
            font-size: clamp(2rem, 4vw, 4.2rem);
            line-height: 0.95;
            font-weight: 950;
        }
        .page-header p {
            max-width: 45rem;
            margin: 0.75rem 0 0 0;
            color: var(--app-muted);
            font-size: 1rem;
            line-height: 1.55;
        }
        .page-header-side {
            min-width: 13rem;
            text-align: right;
            color: var(--app-muted);
            font-size: 0.86rem;
            line-height: 1.45;
        }
        .section-pill {
            display: inline-block;
            padding: 0.32rem 0.7rem;
            border-radius: 999px;
            background: rgba(18, 100, 71, 0.10);
            color: var(--app-green);
            font-size: 0.78rem;
            font-weight: 800;
            letter-spacing: 0.04em;
            text-transform: uppercase;
            margin: 0.65rem 0 0.55rem 0;
            border: 1px solid rgba(18, 100, 71, 0.12);
        }
        div[data-testid="stVerticalBlockBorderWrapper"] > div {
            border-color: var(--app-line);
            border-radius: 8px;
            background:
                linear-gradient(180deg, rgba(255, 255, 255, 0.96), rgba(248, 251, 243, 0.92));
            box-shadow: 0 18px 38px rgba(22, 32, 27, 0.09);
        }
        div[data-testid="stVerticalBlockBorderWrapper"] > div::before {
            content: "";
            display: block;
            height: 4px;
            margin: -1rem -1rem 0.9rem -1rem;
            border-radius: 8px 8px 0 0;
            background: linear-gradient(90deg, var(--app-green), var(--app-gold), rgba(255, 255, 255, 0));
        }
        div[data-testid="stMetric"] {
            border: 1px solid var(--app-line);
            border-radius: 8px;
            padding: 0.78rem 0.88rem;
            background:
                linear-gradient(180deg, rgba(255, 255, 255, 0.96), rgba(250, 252, 247, 0.92));
            box-shadow: 0 10px 26px rgba(22, 32, 27, 0.07);
            min-height: 6.1rem;
        }
        div[data-testid="stMetric"] label {
            color: var(--app-muted);
            font-weight: 700;
        }
        div[data-testid="stMetric"] [data-testid="stMetricValue"] {
            color: var(--app-ink);
            font-size: 1.75rem;
            font-weight: 800;
            overflow-wrap: anywhere;
            white-space: normal;
        }
        div[data-testid="stDataFrame"],
        div[data-testid="stTable"] {
            border-radius: 8px;
            overflow: hidden;
            border: 1px solid var(--app-line);
            box-shadow: 0 10px 28px rgba(22, 32, 27, 0.05);
        }
        .stButton > button {
            border-radius: 8px;
            border: 1px solid rgba(18, 100, 71, 0.16);
            font-weight: 800;
            min-height: 2.75rem;
            box-shadow: 0 10px 24px rgba(18, 100, 71, 0.12);
        }
        .stButton > button[kind="primary"] {
            background: linear-gradient(135deg, var(--app-green), #153d2e);
        }
        .stButton > button:hover {
            border-color: rgba(18, 100, 71, 0.45);
            box-shadow: 0 14px 28px rgba(18, 100, 71, 0.18);
            transform: translateY(-1px);
        }
        [data-testid="stSidebar"] {
            background: #f4f7ee;
        }
        [data-testid="stSidebar"] h1,
        [data-testid="stSidebar"] h2,
        [data-testid="stSidebar"] h3,
        [data-testid="stSidebar"] p,
        [data-testid="stSidebar"] label {
            letter-spacing: 0;
        }
        [data-testid="stSidebar"] hr {
            border-color: rgba(22, 32, 27, 0.12);
        }
        [data-testid="stSidebar"] [role="radiogroup"] {
            gap: 0.35rem;
        }
        [data-testid="stSidebar"] [role="radiogroup"] label {
            padding: 0.36rem 0.45rem;
            border-radius: 8px;
        }
        [data-testid="stSidebar"] [role="radiogroup"] label:hover {
            background: rgba(18, 100, 71, 0.08);
        }
        div[data-baseweb="select"] > div,
        div[data-baseweb="input"] > div,
        textarea {
            border-radius: 8px;
            border-color: rgba(22, 32, 27, 0.14);
            background-color: rgba(255, 255, 255, 0.96);
        }
        button[data-baseweb="tab"] {
            border-radius: 8px 8px 0 0;
            font-weight: 800;
        }
        button[data-baseweb="tab"][aria-selected="true"] {
            background: rgba(18, 100, 71, 0.10);
            color: var(--app-green);
        }
        div[data-testid="stAlert"] {
            border-radius: 8px;
            border: 1px solid rgba(22, 32, 27, 0.08);
        }
        /* Hide Streamlit built-in page navigation (keep our custom menu) */
        div[data-testid="stSidebarNav"] {
            display: none !important;
        }

        /* Force sidebar expanded and visible (override client-side collapse) */
        [data-testid="stSidebar"] {
            width: 260px !important;
            min-width: 260px !important;
            transform: none !important;
        }
        [data-testid="stSidebar"] > div,
        [data-testid="stSidebarContent"] {
            display: block !important;
            opacity: 1 !important;
            visibility: visible !important;
            transform: none !important;
        }
        [data-testid="stSidebarNavItems"] {
            display: block !important;
        }
        /* Mobile portrait and small phones */
        @media (max-width: 600px) {
            .block-container {
                padding-top: 0.65rem;
                padding-left: 0.62rem;
                padding-right: 0.62rem;
                padding-bottom: 1.8rem;
            }
            .page-header {
                display: block;
                margin-bottom: 0.85rem;
                padding-bottom: 0.75rem;
            }
            .page-header-side {
                min-width: 0;
                margin-top: 0.75rem;
                text-align: left;
            }
            h1 {
                font-size: 1.9rem !important;
                line-height: 1.08 !important;
            }
            h2 {
                font-size: 1.35rem !important;
            }
            h3 {
                font-size: 1.12rem !important;
            }
            div[data-testid="stMetric"] {
                min-height: auto;
                padding: 0.65rem 0.72rem;
            }
            div[data-testid="stMetric"] [data-testid="stMetricValue"] {
                font-size: 1.28rem;
            }
            .stButton > button {
                min-height: 2.55rem;
                padding-left: 0.65rem;
                padding-right: 0.65rem;
            }
            div[data-testid="stDataFrame"],
            div[data-testid="stTable"] {
                max-width: 100%;
                overflow-x: auto;
            }
            div[data-testid="stVerticalBlockBorderWrapper"] > div {
                box-shadow: 0 10px 22px rgba(22, 32, 27, 0.07);
            }
            div[data-testid="stVerticalBlockBorderWrapper"] > div::before {
                margin-left: -0.75rem;
                margin-right: -0.75rem;
            }
        }

        /* Tablets and large phones */
        @media (min-width: 601px) and (max-width: 900px) {
            .block-container {
                padding-top: 0.9rem;
                padding-left: 0.9rem;
                padding-right: 0.9rem;
            }
            h1 {
                font-size: 2.25rem !important;
                line-height: 1.08 !important;
            }
            div[data-testid="stMetric"] [data-testid="stMetricValue"] {
                font-size: 1.38rem;
            }
        }

        /* Shared touch layout */
        @media (max-width: 900px) {
            [data-testid="stSidebar"] {
                width: 100% !important;
                min-width: 0 !important;
                max-width: 100% !important;
                border-right: 0;
                border-bottom: 1px solid rgba(22, 32, 27, 0.10);
            }
            [data-testid="stSidebarContent"] {
                padding: 0.75rem;
            }
            [data-testid="stSidebar"] .stButton > button {
                min-height: 2.35rem;
                margin: 0.16rem 0;
                font-size: 0.9rem;
            }
            .app-rail-brand,
            .sidebar-brand {
                margin-bottom: 0.55rem !important;
                padding: 0.65rem !important;
            }
            .app-rail-mark,
            .sidebar-mark {
                width: 2.15rem !important;
                height: 2.15rem !important;
                font-size: 0.78rem !important;
            }
            .app-rail-title,
            .sidebar-brand h2 {
                font-size: 0.95rem !important;
            }
            .app-rail-subtitle,
            .sidebar-brand p {
                font-size: 0.72rem !important;
            }
        }

        /* Small laptops and landscape tablets */
        @media (min-width: 901px) and (max-width: 1100px) {
            .block-container {
                padding-left: 1rem;
                padding-right: 1rem;
            }
            div[data-testid="stMetric"] [data-testid="stMetricValue"] {
                font-size: 1.45rem;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_page_navigation():
    st.sidebar.title("Prono insight")
    st.sidebar.markdown("---")
    links = [
        ("app.py", "Tableau de bord"),
        ("pages/analyse_match.py", "Analyse & comparaison"),
        ("pages/data_management.py", "Mise à jour"),
        ("pages/matchs_a_venir.py", "Matchs à venir"),
        ("pages/prediction_ia.py", "Prédictions"),
    ]
    for page, label in links:
        try:
            st.sidebar.page_link(page, label=label)
        except Exception:
            pass


def page_hero(title: str, description: str):
    inject_app_style()
    st.caption("Football intelligence")
    st.title(title)
    st.write(description)
    st.divider()


def dashboard_hero(title: str, description: str, stats: list[tuple[str, str]]):
    inject_app_style()
    st.caption("Football intelligence")
    st.title(title)
    st.write(description)
    cols = st.columns(len(stats))
    for col, (label, value) in zip(cols, stats):
        col.metric(label, value)
    st.divider()


def section_label(label: str):
    st.markdown(f"### {label}")


def kpi_grid(cards: list[dict]):
    for start in range(0, len(cards), 3):
        cols = st.columns(3)
        for col, card in zip(cols, cards[start:start + 3]):
            with col.container(border=True):
                st.metric(str(card.get("label", "")), str(card.get("value", "")))
                caption = card.get("caption")
                if caption:
                    st.caption(str(caption))


def dashboard_band(insight: str, scope_items: list[tuple[str, str]]):
    left, right = st.columns([1.3, 1])
    with left.container(border=True):
        st.markdown("### Lecture rapide")
        st.write(insight)
    with right.container(border=True):
        st.markdown("### Couverture de la base")
        for label, value in scope_items:
            st.caption(label)
            st.write(f"**{value}**")


def season_summary(title: str, subtitle: str, cards: list[tuple[str, str]], rows: list[dict]):
    with st.container(border=True):
        st.subheader(title)
        st.caption(subtitle)
        cols = st.columns(len(cards))
        for col, (label, value) in zip(cols, cards):
            col.metric(label, value)

        table_rows = [
            {
                "Saison sportive": season_period(row.get("season")),
                "Matchs terminés": row.get("matches", 0),
                "Buts / match": row.get("avg_goals", ""),
            }
            for row in rows
        ]
        st.dataframe(table_rows, hide_index=True, width="stretch")


def run_direct_page(title: str, show_func):
    try:
        st.set_page_config(page_title=title, layout="wide")
    except Exception:
        pass

    from components import auth, sidebar
    from services import background_jobs, import_service, schema_guard

    inject_app_style()

    if not auth.is_authenticated():
        auth.login_page()
        st.stop()

    import_service.init_db()
    schema_guard.ensure_match_score_columns()
    background_jobs.start_startup_updates_once()

    current_nav = {
        "Prono insight": "Tableau de bord",
        "Widgets Live": "Widgets Live",
        "Mise à jour": "Mise à jour",
        "Matchs à venir": "Matchs à venir",
        "Analyse & comparaison": "Analyse & comparaison",
        "Prédictions": "Prédictions",
    }.get(title, "Tableau de bord")

    sidebar.render_app_rail(current_nav)

    with st.sidebar:
        st.caption(f"Connecté: {st.session_state.get('auth_user', 'utilisateur')}")
        auth.logout_button()
        render_background_jobs()

    show_func()


def render_background_jobs():
    from services import background_jobs

    jobs = background_jobs.active_jobs()
    if not jobs:
        return
    st.markdown("---")
    st.markdown("### Téléchargements")
    for job in jobs:
        st.caption(job.get("label", "Tâche en arrière-plan"))
        st.progress(float(job.get("progress") or 0), text=job.get("message") or "En cours...")
