"""Archive les prédictions internes avant le coup d'envoi.

Cette voie ne consomme aucun appel API : elle utilise uniquement l'historique
déjà persisté et la même chaîne de décision que les pages de l'application.
"""

from __future__ import annotations

import datetime
import math

import pandas as pd
from sqlalchemy import text

from database.database import engine
from services import analysis_store, final_prediction_service, prediction_helpers


DEFAULT_HORIZON_DAYS = 14
DEFAULT_MIN_HISTORY_MATCHES = 30
DEFAULT_MAX_FIXTURES = 500


def _utc_datetime(value=None) -> datetime.datetime:
    if value is None:
        value = datetime.datetime.now(datetime.UTC)
    if isinstance(value, str):
        parsed = pd.to_datetime(value, errors="coerce", utc=True)
        if pd.isna(parsed):
            raise ValueError("Date de référence invalide")
        value = parsed.to_pydatetime()
    elif isinstance(value, pd.Timestamp):
        value = value.to_pydatetime()
    if not isinstance(value, datetime.datetime):
        raise TypeError("La date de référence doit être une date")
    if value.tzinfo is None:
        return value.replace(tzinfo=datetime.UTC)
    return value.astimezone(datetime.UTC)


def archive_upcoming_predictions(
    *,
    days: int = DEFAULT_HORIZON_DAYS,
    min_history_matches: int = DEFAULT_MIN_HISTORY_MATCHES,
    max_fixtures: int = DEFAULT_MAX_FIXTURES,
    now=None,
    db_engine=None,
    progress_callback=None,
) -> dict:
    """Calcule et enregistre les prévisions internes futures, sans fuite temporelle.

    Seules les fixtures futures sans score sont retenues. L'historique fourni au
    modèle est strictement antérieur au coup d'envoi et les fixtures avec un
    historique trop court sont reportées plutôt que prédites artificiellement.
    Les écritures de l'historique sont idempotentes via analysis_store.
    """
    reference = _utc_datetime(now)
    horizon_days = max(1, int(days))
    minimum_history = max(1, int(min_history_matches))
    fixture_limit = max(1, int(max_fixtures))
    horizon = reference + datetime.timedelta(days=horizon_days)
    recheck_time = _utc_datetime() if now is None else reference
    source_engine = db_engine or engine
    params = {
        "now": reference.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S"),
        "horizon": horizon.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S"),
        "limit": fixture_limit,
    }
    rows = pd.read_sql(
        text(
            """
            SELECT m.fixture_id, m.league_id, m.season, m.date,
                   m.home_team_id, m.away_team_id,
                   COALESCE(home.name, 'Equipe ' || m.home_team_id) AS home_name,
                   COALESCE(away.name, 'Equipe ' || m.away_team_id) AS away_name
            FROM matches m
            LEFT JOIN teams home ON home.id = m.home_team_id
            LEFT JOIN teams away ON away.id = m.away_team_id
            WHERE datetime(m.date) > datetime(:now)
              AND datetime(m.date) <= datetime(:horizon)
              AND m.home_goals IS NULL
              AND m.away_goals IS NULL
            ORDER BY m.date ASC, m.fixture_id ASC
            LIMIT :limit
            """
        ),
        source_engine,
        params=params,
    )

    summary = {
        "status": "complete",
        "horizon_days": horizon_days,
        "min_history_matches": minimum_history,
        "fixtures_considered": int(len(rows)),
        "archived": 0,
        "skipped_started": 0,
        "skipped_insufficient_history": 0,
        "errors": [],
        "api_calls": 0,
        "reference_time": reference.isoformat(),
    }
    total = len(rows)

    def report_progress(index: int) -> None:
        if progress_callback:
            progress_callback(
                index,
                max(1, total),
                f"Archivage pré-match : {index}/{total}",
            )

    for index, fixture in enumerate(rows.to_dict("records"), start=1):
        fixture_id = int(fixture["fixture_id"])
        kickoff = _utc_datetime(fixture["date"])
        # Recheck immediately before the calculation: a queued fixture may
        # have crossed kickoff after the SQL snapshot was read.
        if kickoff <= recheck_time:
            summary["skipped_started"] += 1
            report_progress(index)
            continue
        try:
            historical = prediction_helpers.load_historical_context(
                int(fixture["league_id"]),
                kickoff.isoformat(),
            )
            if len(historical) < minimum_history:
                summary["skipped_insufficient_history"] += 1
                report_progress(index)
                continue

            final = final_prediction_service.calculate(
                historical,
                int(fixture["home_team_id"]),
                int(fixture["away_team_id"]),
                str(fixture["home_name"]),
                str(fixture["away_name"]),
                player_intelligence=None,
                api_signal=None,
                score_top_n=12,
                match_date=kickoff.isoformat(),
            )
            prediction = final.get("prediction") or {}
            score_prediction = final.get("score_prediction") or {}
            probabilities = [
                float(prediction[key])
                for key in (
                    "home_probability",
                    "draw_probability",
                    "away_probability",
                )
            ]
            if (
                not all(math.isfinite(value) and 0.0 <= value <= 100.0 for value in probabilities)
                or abs(sum(probabilities) - 100.0) > 0.08
                or not score_prediction.get("matrix")
            ):
                raise ValueError("Distribution 1/N/2 ou matrice de scores invalide")

            analysis_store.save_analysis_snapshot(
                analysis_type="prédiction_automatique_pré-match",
                fixture_id=fixture_id,
                league_id=int(fixture["league_id"]),
                season=int(fixture["season"]),
                home_team_id=int(fixture["home_team_id"]),
                away_team_id=int(fixture["away_team_id"]),
                prediction=prediction,
                score_prediction=score_prediction,
                model_details=final.get("model_details") or {},
                context={
                    "capture_mode": "automatic_pre_match",
                    "kickoff": kickoff.isoformat(),
                    "historical_match_count": int(len(historical)),
                    "home_name": str(fixture["home_name"]),
                    "away_name": str(fixture["away_name"]),
                    "api_signal_used": False,
                },
            )
            summary["archived"] += 1
        except Exception as exc:
            summary["errors"].append(f"fixture:{fixture_id}: {exc}")
        report_progress(index)

    if summary["errors"]:
        summary["status"] = "partial"
    return summary
