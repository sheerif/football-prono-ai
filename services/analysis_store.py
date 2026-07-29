import datetime
import json
import math

from database import models
from database.database import SessionLocal, engine


_schema_ready = False


def _ensure_schema():
    global _schema_ready
    if not _schema_ready:
        models.Base.metadata.create_all(bind=engine)
        _schema_ready = True


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
        session.commit()
    return scope_key
