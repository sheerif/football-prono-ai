"""Page dédiée aux exports PDF de pronostics."""

from __future__ import annotations

import re

import pandas as pd
import streamlit as st

from components import ui
from services import pdf_report_service
from services.season_format import season_period


def _round_key(value: object) -> int:
    match = re.search(r"(\d+)$", str(value or ""))
    return int(match.group(1)) if match else 999


def show() -> None:
    ui.page_hero("Rapports PDF", "Exportez une journée, un mois ou votre propre sélection de matchs.")
    st.info("Le PDF comprend 1/N/2, score probable, buts projetés, solidité, risque et recommandation.")
    leagues = pdf_report_service.available_leagues()
    if leagues.empty:
        st.warning("Aucune donnée de match disponible.")
        return
    ids = leagues["id"].astype(int).tolist()
    labels = {int(row.id): f"{row.name} — {row.country}".strip(" —") for row in leagues.itertuples(index=False)}
    left, right = st.columns(2)
    with left:
        league_id = st.selectbox("Ligue", ids, index=ids.index(61) if 61 in ids else 0, format_func=lambda item: labels[int(item)])
    with right:
        season = st.selectbox("Saison sportive", pdf_report_service.available_seasons(league_id))
    fixtures = pdf_report_service.load_fixtures(league_id, season)
    fixtures["_round"] = fixtures["api_round"].map(pdf_report_service.round_label)
    fixtures["_month"] = pd.to_datetime(fixtures["date"], errors="coerce").dt.strftime("%m/%Y")
    mode = st.radio("Périmètre", ("Journée complète", "Mois complet", "Sélection de matchs"), horizontal=True)
    if mode == "Journée complète":
        title = st.selectbox("Journée", sorted(fixtures["_round"].unique(), key=_round_key))
        selected = fixtures[fixtures["_round"] == title]
    elif mode == "Mois complet":
        month = st.selectbox("Mois", sorted(fixtures["_month"].dropna().unique()))
        title, selected = f"Mois {month}", fixtures[fixtures["_month"] == month]
    else:
        options = {int(row.fixture_id): f"{pd.to_datetime(row.date).strftime('%d/%m %H:%M')} — {row.home_name} vs {row.away_name}" for row in fixtures.itertuples(index=False)}
        chosen = st.multiselect("Matchs", list(options), format_func=lambda item: options[int(item)])
        title, selected = "Sélection personnalisée", fixtures[fixtures["fixture_id"].isin(chosen)]
    st.metric("Matchs inclus", len(selected))
    if selected.empty:
        st.caption("Sélectionnez au moins un match.")
        return
    signature = ",".join(str(value) for value in sorted(selected["fixture_id"].astype(int)))
    key = f"{league_id}-{season}-{signature}"
    if st.button("Générer le PDF", type="primary", width="stretch"):
        progress_bar = st.progress(0.0, text="0 % — Préparation du rapport")
        progress_status = st.empty()

        def update_progress(current, total, label):
            ratio = min(0.9, 0.9 * current / max(1, total))
            progress_bar.progress(ratio, text=f"{int(round(ratio * 100))} % — {label}")
            progress_status.caption(f"Traitement : {current}/{total} · {label}")

        try:
            reports = pdf_report_service.build_fixture_reports(
                selected,
                progress_callback=update_progress,
            )
            progress_bar.progress(0.95, text="95 % — Mise en page du document PDF")
            progress_status.caption("Création du fichier téléchargeable…")
            st.session_state["report_pdf_bytes"] = pdf_report_service.build_pdf(reports, league=labels[league_id], season=season_period(season), round_name=title)
            st.session_state["report_pdf_key"] = key
        except Exception as exc:
            progress_bar.progress(1.0, text="Traitement interrompu")
            progress_status.error(f"Impossible de générer le PDF : {exc}")
        else:
            progress_bar.progress(1.0, text="100 % — Rapport PDF terminé")
            progress_status.success(f"Rapport de {len(reports)} match(s) prêt.")
    if st.session_state.get("report_pdf_key") == key:
        safe = re.sub(r"[^a-z0-9-]+", "-", title.lower()).strip("-")
        st.success("Rapport prêt à télécharger.")
        st.download_button("Télécharger le rapport PDF", st.session_state["report_pdf_bytes"], file_name=f"prono-insight-{league_id}-{season}-{safe or 'rapport'}.pdf", mime="application/pdf", width="stretch")


if __name__ == "__main__":
    ui.run_direct_page("Rapports PDF", show)
