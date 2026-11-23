import datetime
import inspect
import sys

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


def _ensure_visual_kpi_compatibility():
    """Adapte un ancien ui.kpi_grid déjà chargé, sans déclencher d'import circulaire."""
    # Importer components.ui ici est dangereux : pendant l'initialisation de
    # services, ui importe services.season_format et peut deadlocker le module.
    ui = sys.modules.get("components.ui")
    if ui is None:
        return

    try:
        supports_columns = "columns" in inspect.signature(ui.kpi_grid).parameters
    except (AttributeError, TypeError, ValueError):
        supports_columns = False
    if supports_columns:
        return

    legacy_kpi_grid = ui.kpi_grid

    def compatible_kpi_grid(cards, columns=3):
        return legacy_kpi_grid(cards)

    ui.kpi_grid = compatible_kpi_grid


_ensure_projected_lineup_model()
_ensure_visual_kpi_compatibility()

from . import analysis_store, api_football, import_service, lineup_service, player_service, prediction_helpers, prediction_service, stats_service

__all__ = ["analysis_store", "api_football", "import_service", "lineup_service", "player_service", "prediction_helpers", "prediction_service", "stats_service"]
