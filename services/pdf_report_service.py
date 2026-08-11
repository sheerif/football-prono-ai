"""Extraction de prédictions et génération de rapports PDF de journées."""

from __future__ import annotations

import io
from collections.abc import Iterable

import pandas as pd
from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from sqlalchemy import text

from database.database import engine
from services import cross_insight_service, final_prediction_service, lineup_service


NAVY = HexColor("#071a2b")
TEAL = HexColor("#2aa198")
MUTED = HexColor("#5f7082")
PAPER = HexColor("#f5f8fa")
GREEN = HexColor("#d8f5e5")
ORANGE = HexColor("#fff0cf")
RED = HexColor("#f9d9d8")


def available_leagues() -> pd.DataFrame:
    """Retourne les championnats disposant d'au moins une fixture en base."""
    return pd.read_sql(
        text(
            """
            SELECT m.league_id AS id, COALESCE(l.name, 'Championnat ' || m.league_id) AS name,
                   COALESCE(l.country, '') AS country
            FROM matches m LEFT JOIN leagues l ON l.id = m.league_id
            GROUP BY m.league_id, l.name, l.country ORDER BY name
            """
        ),
        engine,
    )


def available_seasons(league_id: int) -> list[int]:
    rows = pd.read_sql(
        text("SELECT DISTINCT season FROM matches WHERE league_id = :league_id ORDER BY season DESC"),
        engine,
        params={"league_id": int(league_id)},
    )
    return [int(value) for value in rows["season"].dropna().tolist()]


def load_fixtures(league_id: int, season: int) -> pd.DataFrame:
    """Extrait toutes les fixtures d'une saison avec leurs métadonnées utiles."""
    return pd.read_sql(
        text(
            """
            SELECT m.fixture_id, m.league_id, m.season, m.date, m.home_team_id,
                   m.away_team_id, m.home_goals, m.away_goals, m.status,
                   COALESCE(l.name, 'Championnat ' || m.league_id) AS league_name,
                   COALESCE(home.name, 'Equipe ' || m.home_team_id) AS home_name,
                   COALESCE(away.name, 'Equipe ' || m.away_team_id) AS away_name,
                   COALESCE(d.round, '') AS api_round
            FROM matches m
            LEFT JOIN leagues l ON l.id = m.league_id
            LEFT JOIN teams home ON home.id = m.home_team_id
            LEFT JOIN teams away ON away.id = m.away_team_id
            LEFT JOIN fixture_api_details d ON d.fixture_id = m.fixture_id
            WHERE m.league_id = :league_id AND m.season = :season
            ORDER BY m.date, m.fixture_id
            """
        ),
        engine,
        params={"league_id": int(league_id), "season": int(season)},
    )


def round_label(value: object) -> str:
    raw = str(value or "").strip()
    if raw.lower().startswith("regular season -"):
        suffix = raw.rsplit("-", 1)[-1].strip()
        return f"Journée {suffix}" if suffix.isdigit() else raw
    return raw or "Journée non précisée"


def historical_context(league_id: int, kickoff: object) -> pd.DataFrame:
    """Retourne exclusivement les résultats connus avant le coup d'envoi."""
    return pd.read_sql(
        text(
            """
            SELECT * FROM matches
            WHERE league_id = :league_id AND date < :kickoff
              AND home_goals IS NOT NULL AND away_goals IS NOT NULL
            ORDER BY date DESC, fixture_id DESC
            """
        ),
        engine,
        params={"league_id": int(league_id), "kickoff": str(kickoff)},
    )


def build_fixture_reports(fixtures: pd.DataFrame) -> list[dict]:
    """Calcule les rapports sans jamais intégrer un résultat postérieur au match."""
    reports: list[dict] = []
    contexts: dict[str, pd.DataFrame] = {}
    for fixture in fixtures.itertuples(index=False):
        kickoff = str(fixture.date)
        context = contexts.setdefault(
            kickoff, historical_context(int(fixture.league_id), kickoff)
        )
        base = {
            "fixture_id": int(fixture.fixture_id),
            "date": pd.to_datetime(fixture.date, errors="coerce").strftime("%d/%m/%Y %H:%M UTC"),
            "league": str(fixture.league_name),
            "round": round_label(fixture.api_round),
            "home_name": str(fixture.home_name),
            "away_name": str(fixture.away_name),
            "actual_score": (
                f"{int(fixture.home_goals)}-{int(fixture.away_goals)}"
                if pd.notna(fixture.home_goals) and pd.notna(fixture.away_goals)
                else "À venir"
            ),
        }
        if context.empty:
            reports.append({**base, "available": False, "reason": "Historique insuffisant avant ce match."})
            continue
        intelligence = lineup_service.get_match_intelligence(
            fixture_id=int(fixture.fixture_id), home_team_id=int(fixture.home_team_id),
            away_team_id=int(fixture.away_team_id), season=int(fixture.season), match_date=fixture.date,
        )
        final = final_prediction_service.calculate(
            context, int(fixture.home_team_id), int(fixture.away_team_id),
            str(fixture.home_name), str(fixture.away_name),
            player_intelligence=intelligence,
            api_signal=cross_insight_service.load_fixture_api_signal_before(
                int(fixture.fixture_id), fixture.date
            ),
            score_top_n=1, match_date=fixture.date,
        )
        prediction = final["prediction"]
        score = final["score_prediction"]
        scores = score.get("scores") or []
        reports.append({
            **base,
            "available": True,
            "home_probability": float(prediction["home_probability"]),
            "draw_probability": float(prediction["draw_probability"]),
            "away_probability": float(prediction["away_probability"]),
            "score_probable": scores[0]["Score"] if scores else "—",
            "expected_home_goals": float(score.get("expected_home_goals") or 0),
            "expected_away_goals": float(score.get("expected_away_goals") or 0),
            "solidity": float(prediction.get("ranking_score") or 0),
            "risk": str(prediction.get("risk_level") or "modéré"),
            "market": str(prediction.get("recommended_market") or "PRUDENCE"),
        })
    return reports


def build_pdf(reports: Iterable[dict], *, league: str, season: str, round_name: str) -> bytes:
    """Produit un PDF A4 lisible, compact et imprimable pour une journée."""
    output = io.BytesIO()
    pdf = canvas.Canvas(output, pagesize=A4, pageCompression=1)
    width, height = A4
    y = height

    def header():
        nonlocal y
        pdf.setFillColor(NAVY)
        pdf.rect(0, height - 94, width, 94, fill=1, stroke=0)
        pdf.setFillColor(white)
        pdf.setFont("Helvetica-Bold", 20)
        pdf.drawString(36, height - 41, "PRONO INSIGHT — RAPPORT DE JOURNÉE")
        pdf.setFont("Helvetica", 10)
        pdf.drawString(36, height - 62, f"{league}  |  {season}  |  {round_name}")
        pdf.drawRightString(width - 36, height - 62, "Estimations probabilistes — pas des certitudes")
        y = height - 118

    def new_page():
        pdf.showPage()
        header()

    def label(text: str, x: float, top: float, value: str, color=NAVY):
        pdf.setFillColor(MUTED)
        pdf.setFont("Helvetica", 8)
        pdf.drawString(x, top, text.upper())
        pdf.setFillColor(color)
        pdf.setFont("Helvetica-Bold", 11)
        pdf.drawString(x, top - 15, value)

    header()
    for report in reports:
        card_height = 132 if report.get("available") else 92
        if y - card_height < 44:
            new_page()
        pdf.setFillColor(white)
        pdf.setStrokeColor(HexColor("#d8e1e8"))
        pdf.roundRect(32, y - card_height, width - 64, card_height - 4, 9, fill=1, stroke=1)
        pdf.setFillColor(NAVY)
        pdf.setFont("Helvetica-Bold", 13)
        pdf.drawString(48, y - 25, f"{report['home_name']}  —  {report['away_name']}")
        pdf.setFillColor(MUTED)
        pdf.setFont("Helvetica", 8.5)
        pdf.drawString(48, y - 40, f"{report['date']}  ·  Résultat : {report['actual_score']}")
        if not report.get("available"):
            pdf.setFillColor(RED)
            pdf.roundRect(48, y - 76, width - 96, 22, 6, fill=1, stroke=0)
            pdf.setFillColor(NAVY)
            pdf.setFont("Helvetica-Bold", 9)
            pdf.drawString(58, y - 68, report["reason"])
            y -= card_height + 10
            continue
        probabilities = [report["home_probability"], report["draw_probability"], report["away_probability"]]
        colors = [TEAL, HexColor("#d49b38"), HexColor("#173e61")]
        x, bar_width = 48, width - 96
        for probability, color in zip(probabilities, colors, strict=True):
            segment = bar_width * probability / 100
            pdf.setFillColor(color)
            pdf.rect(x, y - 61, segment, 10, fill=1, stroke=0)
            x += segment
        label("1", 48, y - 77, f"{probabilities[0]:.1f} %")
        label("N", 178, y - 77, f"{probabilities[1]:.1f} %")
        label("2", 308, y - 77, f"{probabilities[2]:.1f} %")
        label("Score probable", 48, y - 107, report["score_probable"])
        label("Buts attendus", 188, y - 107, f"{report['expected_home_goals']:.2f} — {report['expected_away_goals']:.2f}")
        label("Solidité", 338, y - 107, f"{report['solidity']:.0f}/100")
        risk_color = GREEN if report["risk"] == "faible" else ORANGE if report["risk"] == "modéré" else RED
        pdf.setFillColor(risk_color)
        pdf.roundRect(442, y - 117, 110, 22, 6, fill=1, stroke=0)
        pdf.setFillColor(NAVY)
        pdf.setFont("Helvetica-Bold", 9)
        pdf.drawCentredString(497, y - 108, f"{report['market']} · risque {report['risk']}")
        y -= card_height + 10
    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica", 7.5)
    pdf.drawString(36, 24, "Les probabilités, scores et buts attendus sont des estimations calculées avec les données disponibles avant chaque match.")
    pdf.save()
    return output.getvalue()
