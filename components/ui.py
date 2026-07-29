import html
import re

import streamlit as st

from services.season_format import season_period


PAGE_ICONS = {
    "Prono insight": "⚽",
    "Tableau de bord": "📊",
    "Mise à jour": "🔄",
    "Joueurs": "👤",
    "Matchs à venir": "🗓️",
    "Analyse": "🔎",
    "Analyse & comparaison": "⚔️",
    "Prédictions": "🎯",
    "Widgets Live": "📡",
}

LABEL_ICONS = {
    "match": "⚽",
    "joueur": "👤",
    "équipe": "🛡️",
    "composition": "🧩",
    "forme": "🔥",
    "but": "🥅",
    "passe": "🎯",
    "minute": "⏱️",
    "victoire": "🏆",
    "nul": "🤝",
    "défaite": "📉",
    "saison": "📅",
    "championnat": "🌍",
    "prédiction": "🔮",
    "probabilité": "📈",
    "confiance": "✅",
    "analyse": "🔎",
    "statistique": "📊",
    "tactique": "🧠",
    "stratégie": "♟️",
    "téléchargement": "⬇️",
    "synchronisation": "🔄",
    "base": "🗄️",
    "carton": "🟨",
    "note": "⭐",
    "attaque": "⚡",
    "défense": "🧱",
}


def _icon_for(label: str, fallback: str = "◆") -> str:
    normalized = str(label or "").lower()
    for token, icon in LABEL_ICONS.items():
        if token in normalized:
            return icon
    return fallback


def _page_icon(title: str) -> str:
    for token, icon in PAGE_ICONS.items():
        if token.lower() in str(title).lower():
            return icon
    return "⚽"


def _progress_value(value) -> float | None:
    raw = str(value or "").replace(",", ".")
    percent = re.search(r"(-?\d+(?:\.\d+)?)\s*%", raw)
    if percent:
        return max(0.0, min(100.0, float(percent.group(1))))
    score = re.search(r"(-?\d+(?:\.\d+)?)\s*/\s*100", raw)
    if score:
        return max(0.0, min(100.0, float(score.group(1))))
    return None


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
        div[data-testid="stMetric"] label::before {
            content: "◆";
            display: inline-grid;
            place-items: center;
            width: 1.35rem;
            height: 1.35rem;
            margin-right: .38rem;
            border-radius: 7px;
            background: rgba(18,100,71,.10);
            color: var(--app-green);
            font-size: .65rem;
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
        .visual-hero {
            position: relative;
            display: grid;
            grid-template-columns: auto minmax(0, 1fr) auto;
            align-items: center;
            gap: 1.15rem;
            margin: .2rem 0 1.35rem;
            padding: 1.15rem 1.3rem;
            overflow: hidden;
            border: 1px solid rgba(18,100,71,.18);
            border-radius: 18px;
            background:
                radial-gradient(circle at 85% 20%, rgba(216,165,40,.26), transparent 13rem),
                linear-gradient(135deg, #102f24, #18563f 62%, #244c3b);
            box-shadow: 0 20px 44px rgba(16,47,36,.18);
            color: white;
        }
        .visual-hero::after {
            content: "";
            position: absolute;
            right: -35px;
            bottom: -70px;
            width: 180px;
            height: 180px;
            border: 2px solid rgba(255,255,255,.16);
            border-radius: 50%;
            box-shadow: 0 0 0 22px rgba(255,255,255,.04);
        }
        .visual-hero-icon {
            display: grid;
            place-items: center;
            width: 4.4rem;
            height: 4.4rem;
            border: 1px solid rgba(255,255,255,.28);
            border-radius: 18px;
            background: linear-gradient(135deg, rgba(185,215,111,.95), rgba(216,165,40,.96));
            box-shadow: 0 12px 28px rgba(0,0,0,.22);
            font-size: 2rem;
        }
        .visual-hero-eyebrow {
            color: #d8e9ab;
            font-size: .68rem;
            font-weight: 900;
            letter-spacing: .12em;
            text-transform: uppercase;
        }
        .visual-hero h1 {
            margin: .15rem 0 .3rem;
            color: white;
            font-size: clamp(1.8rem, 4vw, 3.45rem);
            line-height: 1;
        }
        .visual-hero p {
            max-width: 52rem;
            margin: 0;
            color: rgba(255,255,255,.76);
            line-height: 1.45;
        }
        .visual-hero-ball {
            position: relative;
            z-index: 1;
            font-size: 3.2rem;
            opacity: .82;
            transform: rotate(-12deg);
        }
        .visual-section {
            display: flex;
            align-items: center;
            gap: .55rem;
            margin: 1.25rem 0 .65rem;
            color: var(--app-ink);
            font-size: 1.3rem;
            font-weight: 900;
        }
        .visual-section-icon {
            display: grid;
            place-items: center;
            width: 2rem;
            height: 2rem;
            border-radius: 10px;
            background: linear-gradient(135deg, rgba(18,100,71,.14), rgba(216,165,40,.18));
            box-shadow: inset 0 0 0 1px rgba(18,100,71,.10);
            font-size: 1rem;
        }
        .visual-section::after {
            content: "";
            flex: 1;
            height: 1px;
            background: linear-gradient(90deg, rgba(18,100,71,.22), transparent);
        }
        .visual-kpi {
            position: relative;
            min-height: 8.2rem;
            padding: .92rem 1rem;
            overflow: hidden;
            border: 1px solid rgba(22,32,27,.10);
            border-radius: 15px;
            background: linear-gradient(145deg, rgba(255,255,255,.98), rgba(245,249,239,.95));
            box-shadow: 0 12px 28px rgba(22,32,27,.08);
        }
        .visual-kpi::after {
            content: "";
            position: absolute;
            width: 70px;
            height: 70px;
            right: -24px;
            top: -26px;
            border-radius: 50%;
            background: var(--kpi-accent);
            opacity: .10;
        }
        .visual-kpi-head {
            display: flex;
            align-items: center;
            gap: .45rem;
            color: var(--app-muted);
            font-size: .75rem;
            font-weight: 800;
        }
        .visual-kpi-icon {
            display: grid;
            place-items: center;
            width: 1.85rem;
            height: 1.85rem;
            border-radius: 9px;
            background: color-mix(in srgb, var(--kpi-accent) 16%, white);
            font-size: .95rem;
        }
        .visual-kpi-value {
            margin-top: .36rem;
            color: var(--app-ink);
            font-size: clamp(1.35rem, 2.4vw, 2rem);
            font-weight: 950;
            line-height: 1.05;
        }
        .visual-kpi-caption {
            margin-top: .32rem;
            color: var(--app-muted);
            font-size: .69rem;
            line-height: 1.25;
        }
        .visual-kpi-track {
            height: 5px;
            margin-top: .55rem;
            overflow: hidden;
            border-radius: 999px;
            background: rgba(22,32,27,.08);
        }
        .visual-kpi-fill {
            height: 100%;
            border-radius: inherit;
            background: var(--kpi-accent);
        }
        [data-testid="stProgress"] > div > div > div {
            background: linear-gradient(90deg, var(--app-green), var(--app-gold));
        }
        button[data-baseweb="tab"] {
            border: 1px solid transparent;
        }
        button[data-baseweb="tab"][aria-selected="true"] {
            box-shadow: inset 0 -3px 0 var(--app-green);
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
            .visual-hero {
                grid-template-columns: auto 1fr;
                gap: .75rem;
                padding: .85rem;
                border-radius: 14px;
            }
            .visual-hero-icon { width: 3.2rem; height: 3.2rem; font-size: 1.45rem; }
            .visual-hero-ball { display: none; }
            .visual-hero h1 { font-size: 1.65rem !important; }
            .visual-kpi { min-height: 7rem; padding: .75rem; }
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
        ("pages/joueurs.py", "Joueurs"),
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
    safe_title = html.escape(str(title))
    safe_description = html.escape(str(description))
    st.html(
        f"""
        <div class="visual-hero">
            <div class="visual-hero-icon">{_page_icon(title)}</div>
            <div>
                <div class="visual-hero-eyebrow">Football intelligence</div>
                <h1>{safe_title}</h1>
                <p>{safe_description}</p>
            </div>
            <div class="visual-hero-ball">⚽</div>
        </div>
        """
    )


def dashboard_hero(title: str, description: str, stats: list[tuple[str, str]]):
    page_hero(title, description)
    kpi_grid(
        [
            {"label": label, "value": value, "caption": "Vue synthétique"}
            for label, value in stats
        ],
        columns=min(4, max(1, len(stats))),
    )


def section_label(label: str):
    st.html(
        f"""
        <div class="visual-section">
            <span class="visual-section-icon">{_icon_for(label)}</span>
            <span>{html.escape(str(label))}</span>
        </div>
        """
    )


def kpi_grid(cards: list[dict], columns: int = 3):
    palette = ["#126447", "#d8a528", "#4d7c8a", "#7a5c96", "#c94b3f"]
    columns = max(1, int(columns))
    for start in range(0, len(cards), columns):
        cols = st.columns(columns)
        for offset, (col, card) in enumerate(zip(cols, cards[start:start + columns])):
            label = str(card.get("label", ""))
            value = str(card.get("value", ""))
            caption = str(card.get("caption") or "")
            accent = str(card.get("accent") or palette[(start + offset) % len(palette)])
            progress = _progress_value(value)
            track = (
                f'<div class="visual-kpi-track"><div class="visual-kpi-fill" '
                f'style="width:{progress:.1f}%"></div></div>'
                if progress is not None
                else ""
            )
            col.html(
                f"""
                <div class="visual-kpi" style="--kpi-accent:{html.escape(accent, quote=True)}">
                    <div class="visual-kpi-head">
                        <span class="visual-kpi-icon">{card.get("icon") or _icon_for(label)}</span>
                        <span>{html.escape(label)}</span>
                    </div>
                    <div class="visual-kpi-value">{html.escape(value)}</div>
                    {track}
                    <div class="visual-kpi-caption">{html.escape(caption)}</div>
                </div>
                """
            )


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


def render_cross_insight(insight: dict):
    section_label("Lecture croisée")
    st.caption(
        "Synthèse commune de la forme, du rendement domicile/extérieur, des "
        "face-à-face, du modèle probabiliste et des buts attendus."
    )
    kpi_grid(
        [
            {
                "label": "Tendance",
                "value": insight["verdict"],
                "caption": "Synthèse des signaux",
                "icon": "🧭",
            },
            {
                "label": "Écart synthétique",
                "value": f"{abs(insight['edge'])} points",
                "caption": "Amplitude de l’avantage",
                "icon": "⚖️",
            },
            {
                "label": "Fiabilité des données",
                "value": f"{insight['reliability']} %",
                "caption": insight["reliability_label"],
                "icon": "✅",
            },
        ]
    )

    if insight["verdict"] == "Match équilibré":
        st.warning(
            "Les signaux restent proches : aucun scénario ne domine clairement."
        )
    elif insight["opposing_factors"]:
        st.info(
            f"{insight['verdict']}, mais la lecture reste nuancée car certains "
            "indicateurs vont dans le sens inverse."
        )
    else:
        st.success(
            f"{insight['verdict']} : les principaux indicateurs convergent."
        )

    st.markdown("#### Détail visuel des signaux")
    for start in range(0, len(insight["factors"]), 2):
        columns = st.columns(2)
        for column, factor in zip(columns, insight["factors"][start:start + 2]):
            strength = max(0.0, min(100.0, float(factor["strength"])))
            intensity = (
                "Signal marqué" if strength >= 35
                else "Signal modéré" if strength >= 15
                else "Signal léger"
            )
            factor_name = str(factor["factor"])
            column.html(
                f"""
                <div class="visual-kpi" style="--kpi-accent:#126447;min-height:7.4rem">
                    <div class="visual-kpi-head">
                        <span class="visual-kpi-icon">{_icon_for(factor_name, "📍")}</span>
                        <span>{html.escape(factor_name)}</span>
                    </div>
                    <div style="margin-top:.45rem;font-weight:850;color:#16201b">
                        {html.escape(str(factor["advantage"]))}
                    </div>
                    <div class="visual-kpi-track">
                        <div class="visual-kpi-fill" style="width:{strength:.1f}%"></div>
                    </div>
                    <div class="visual-kpi-caption">{intensity} · {strength:.0f}/100</div>
                </div>
                """
            )

    with st.expander("Réserves et limites de cette lecture"):
        if insight["caveats"]:
            for caveat in insight["caveats"]:
                st.write(f"- {caveat}")
        else:
            st.write("Aucune réserve majeure détectée sur les données disponibles.")
        st.caption(
            "Cette synthèse reste statistique et ne connaît pas automatiquement "
            "les blessures, suspensions, compositions ou conditions météo."
        )


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
        "Joueurs": "Joueurs",
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
