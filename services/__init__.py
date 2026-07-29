import datetime

from sqlalchemy import Column, DateTime, Float, Integer, String, Text


def _ensure_projected_lineup_model():
    """Répare les sessions Streamlit ayant conservé un ancien module models."""
    from database import models

    if hasattr(models, "ProjectedLineup"):
        return

    class ProjectedLineup(models.Base):
        __tablename__ = "projected_lineups"
        __table_args__ = {"extend_existing": True}

        scope_key = Column(String, primary_key=True)
        fixture_id = Column(Integer, nullable=True, index=True)
        team_id = Column(Integer, nullable=False, index=True)
        league_id = Column(Integer, nullable=False, index=True)
        season = Column(Integer, nullable=False, index=True)
        formation = Column(String, nullable=True)
        confidence = Column(Float, nullable=False, default=0.0)
        formation_source = Column(Text, nullable=True)
        player_source = Column(Text, nullable=True)
        strategy = Column(Text, nullable=True)
        lineup_json = Column(Text, nullable=False)
        updated_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)

    models.ProjectedLineup = ProjectedLineup


_ensure_projected_lineup_model()

from . import analysis_store, api_football, import_service, lineup_service, player_service, prediction_helpers, prediction_service, stats_service

__all__ = ["analysis_store", "api_football", "import_service", "lineup_service", "player_service", "prediction_helpers", "prediction_service", "stats_service"]
