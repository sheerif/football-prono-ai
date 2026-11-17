import datetime
import hashlib
import json
import math

from database import models
from database.database import SessionLocal, engine


_schema_ready = False
PREDICTION_MODEL_VERSION = "poisson-decision-engine-v1"


def _ensure_schema():
    global _schema_ready
    if not _schema_ready:
        models.Base.metadata.create_all(bind=engine)
        _backfill_legacy_prediction_snapshots()
        _schema_ready = True


def _backfill_legacy_prediction_snapshots() -> int:
    """Import valid internal predictions from snapshots created before history existed."""
    inserted = 0
    with SessionLocal() as session:
        snapshots = (
            session.query(models.MatchAnalysisSnapshot)
            .order_by(models.MatchAnalysisSnapshot.updated_at.asc())
            .all()
        )
        for snapshot in snapshots:
            try:
                prediction = json.loads(snapshot.prediction_json or "{}")
                model_details = json.loads(snapshot.model_details_json or "{}")
                context = json.loads(snapshot.context_json or "{}")
                if not isinstance(prediction, dict):
                    continue
                is_new = _save_prediction_record(
                    session,
                    analysis_type=snapshot.analysis_type,
                    league_id=int(snapshot.league_id or 0),
                    season=int(snapshot.season or 0),
                    home_team_id=int(snapshot.home_team_id),
                    away_team_id=int(snapshot.away_team_id),
                    prediction=prediction,
                    fixture_id=snapshot.fixture_id,
                    model_details=model_details if isinstance(model_details, dict) else {},
                    context=context if isinstance(context, dict) else {},
                    model_version="legacy-unknown",
                    source="internal_legacy",
                    created_at=snapshot.updated_at,
                )
                inserted += int(is_new)
            except (TypeError, ValueError, json.JSONDecodeError):
                # A malformed historical snapshot must not block app startup.
                continue
        session.commit()
    return inserted


def _clean(value):
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): _clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_clean(item) for item in value]
    if hasattr(value, "item"):
        try:
            return _clean(value.item())
        except Exception:
            pass
    return str(value)


def _json(value) -> str | None:
    if value is None:
        return None
    return json.dumps(_clean(value), ensure_ascii=False, allow_nan=False)


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def match_scope_key(
    league_id: int,
    season: int,
    home_team_id: int,
    away_team_id: int,
    fixture_id: int | None = None,
) -> str:
    if fixture_id is not None:
        return f"fixture:{int(fixture_id)}"
    return (
        f"match:{int(league_id)}:{int(season)}:"
        f"{int(home_team_id)}:{int(away_team_id)}"
    )


def _save_prediction_record(
    session,
    *,
    analysis_type: str,
    league_id: int,
    season: int,
    home_team_id: int,
    away_team_id: int,
    prediction: dict,
    fixture_id: int | None,
    model_details: dict | None,
    context: dict | None,
    model_version: str | None = None,
    source: str = "internal",
    created_at: datetime.datetime | None = None,
) -> bool:
    """Persist the latest fixture prediction and an idempotent audit history row.

    API forecasts remain in their dedicated API cache tables. Only the internal
    final distribution passed by the analysis flow is written here.
    """
    keys = ("home_probability", "draw_probability", "away_probability")
    try:
        probabilities = [float(prediction[key]) for key in keys]
    except (KeyError, TypeError, ValueError):
        return False
    if (
        not all(math.isfinite(value) and 0.0 <= value <= 100.0 for value in probabilities)
        or sum(probabilities) <= 0
    ):
        return False
    confidence_value = prediction.get("confidence")
    try:
        confidence = float(confidence_value) if confidence_value is not None else None
    except (TypeError, ValueError):
        confidence = None
    if confidence is not None and not math.isfinite(confidence):
        confidence = None

    version = str(model_version or PREDICTION_MODEL_VERSION)
    metadata = {
        "prediction": prediction,
        "model_details": model_details or {},
        "context": context or {},
        "api_predictions_stored_separately": True,
        "legacy_backfill": source == "internal_legacy",
    }
    metadata_json = _json(metadata)
    identity = _json({
        "fixture_id": int(fixture_id) if fixture_id is not None else None,
        "league_id": int(league_id),
        "season": int(season),
        "home_team_id": int(home_team_id),
        "away_team_id": int(away_team_id),
        "analysis_type": str(analysis_type),
        "model_version": version,
        "source": source,
        "probabilities": probabilities,
        "metadata": metadata_json,
    }) or ""
    prediction_key = hashlib.sha256(identity.encode("utf-8")).hexdigest()

    history = (
        session.query(models.PredictionHistory)
        .filter_by(prediction_key=prediction_key)
        .one_or_none()
    )
    is_new = history is None
    if is_new:
        session.add(
            models.PredictionHistory(
                prediction_key=prediction_key,
                fixture_id=int(fixture_id) if fixture_id is not None else None,
                league_id=int(league_id),
                season=int(season),
                home_team_id=int(home_team_id),
                away_team_id=int(away_team_id),
                home_probability=probabilities[0],
                draw_probability=probabilities[1],
                away_probability=probabilities[2],
                confidence=confidence,
                model_version=version,
                source=str(source),
                analysis_type=str(analysis_type),
                metadata_json=metadata_json,
                created_at=created_at or _now(),
            )
        )

    # The legacy predictions table is a current-state view keyed by fixture.
    # Keep it populated for existing consumers while history remains append-only.
    if fixture_id is not None:
        fixture_key = int(fixture_id)
        latest = session.get(models.Prediction, fixture_key)
        if latest is None:
            latest = models.Prediction(fixture_id=fixture_key)
            session.add(latest)
        record_time = created_at or _now()
        if latest.created_at is None or record_time >= latest.created_at:
            latest.home_probability = probabilities[0]
            latest.draw_probability = probabilities[1]
            latest.away_probability = probabilities[2]
            latest.confidence = confidence
            latest.created_at = record_time
    return is_new


def save_analysis_snapshot(
    *,
    analysis_type: str,
    league_id: int,
    season: int,
    home_team_id: int,
    away_team_id: int,
    prediction: dict,
    fixture_id: int | None = None,
    score_prediction: dict | None = None,
    player_intelligence: dict | None = None,
    model_details: dict | None = None,
    cross_insight: dict | None = None,
    context: dict | None = None,
    model_version: str | None = None,
) -> str:
    """Enregistre le dernier état complet d’une analyse de match."""
    _ensure_schema()
    scope_key = match_scope_key(
        league_id,
        season,
        home_team_id,
        away_team_id,
        fixture_id=fixture_id,
    )
    tactical = (
        (player_intelligence or {}).get("tactical")
        or (model_details or {}).get("tactical_analysis")
    )
    with SessionLocal() as session:
        row = session.get(models.MatchAnalysisSnapshot, scope_key)
        if row is None:
            row = models.MatchAnalysisSnapshot(
                scope_key=scope_key,
                analysis_type=str(analysis_type),
                home_team_id=int(home_team_id),
                away_team_id=int(away_team_id),
                prediction_json="{}",
            )
        row.analysis_type = str(analysis_type)
        row.fixture_id = int(fixture_id) if fixture_id is not None else None
        row.league_id = int(league_id)
        row.season = int(season)
        row.home_team_id = int(home_team_id)
        row.away_team_id = int(away_team_id)
        row.prediction_json = _json(prediction) or "{}"
        row.score_prediction_json = _json(score_prediction)
        row.player_intelligence_json = _json(player_intelligence)
        row.tactical_analysis_json = _json(tactical)
        row.model_details_json = _json(model_details)
        row.cross_insight_json = _json(cross_insight)
        row.context_json = _json(context)
        row.updated_at = _now()
        session.add(row)
        _save_prediction_record(
            session,
            analysis_type=analysis_type,
            league_id=league_id,
            season=season,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            prediction=prediction,
            fixture_id=fixture_id,
            model_details=model_details,
            context=context,
            model_version=model_version,
        )
        session.commit()
    return scope_key


def ensure_prediction_history() -> None:
    """Ensure prediction tables exist and import valid pre-history snapshots."""
    _ensure_schema()
