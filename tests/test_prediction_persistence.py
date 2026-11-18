import datetime
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import models
from services import analysis_store, draw_calibration_service


class PredictionPersistenceTests(unittest.TestCase):

    def test_model_version_includes_active_draw_calibration(self):
        self.assertEqual(
            analysis_store.PREDICTION_MODEL_VERSION,
            f"poisson-decision-engine-v1+independent-poisson-adaptive-tail-v2+{draw_calibration_service.MODEL_VERSION}",
        )

    def setUp(self):
        self.test_engine = create_engine("sqlite://")
        self.session_factory = sessionmaker(
            autocommit=False, autoflush=False, bind=self.test_engine
        )
        self.schema_patch = patch.object(analysis_store, "engine", self.test_engine)
        self.session_patch = patch.object(
            analysis_store, "SessionLocal", self.session_factory
        )
        self.schema_patch.start()
        self.session_patch.start()
        self.ready_patch = patch.object(analysis_store, "_schema_ready", False)
        self.ready_patch.start()

    def tearDown(self):
        self.ready_patch.stop()
        self.session_patch.stop()
        self.schema_patch.stop()
        self.test_engine.dispose()

    def test_fixture_prediction_is_saved_as_latest_and_history(self):
        arguments = {
            "analysis_type": "aperçu_match_à_venir",
            "league_id": 61,
            "season": 2026,
            "home_team_id": 85,
            "away_team_id": 86,
            "fixture_id": 12345,
            "prediction": {
                "home_probability": 48.0,
                "draw_probability": 27.0,
                "away_probability": 25.0,
                "confidence": 48.0,
                "recommended_market": "1X",
            },
            "model_details": {"decision": {"risk_score": 22.0}},
            "context": {"kickoff": "2026-11-20T19:00:00Z"},
        }

        analysis_store.save_analysis_snapshot(**arguments)
        # A Streamlit rerun with identical inputs must not duplicate history.
        analysis_store.save_analysis_snapshot(**arguments)

        with self.session_factory() as session:
            latest = session.get(models.Prediction, 12345)
            history = session.query(models.PredictionHistory).all()
            snapshots = session.query(models.MatchAnalysisSnapshot).all()

        self.assertIsNotNone(latest)
        self.assertEqual(
            (
                latest.home_probability,
                latest.draw_probability,
                latest.away_probability,
            ),
            (48.0, 27.0, 25.0),
        )
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].fixture_id, 12345)
        self.assertEqual(history[0].source, "internal")
        self.assertEqual(history[0].model_version, analysis_store.PREDICTION_MODEL_VERSION)
        self.assertEqual(len(snapshots), 1)

    def test_existing_analysis_snapshots_are_backfilled_once(self):
        models.Base.metadata.create_all(self.test_engine)
        with self.test_engine.begin() as connection:
            connection.exec_driver_sql("DROP TABLE prediction_history")
        legacy_time = datetime.datetime(2026, 9, 10, 18, 3, 35)
        with self.session_factory() as session:
            session.add(
                models.MatchAnalysisSnapshot(
                    scope_key="fixture:12345",
                    analysis_type="aperçu_match_à_venir",
                    fixture_id=12345,
                    league_id=61,
                    season=2026,
                    home_team_id=85,
                    away_team_id=86,
                    prediction_json=(
                        '{"home_probability":48.0,"draw_probability":27.0,'
                        '"away_probability":25.0,"confidence":48.0}'
                    ),
                    model_details_json='{"historical_match_count": 100}',
                    context_json='{"kickoff":"2026-09-11T18:00:00Z"}',
                    updated_at=legacy_time,
                )
            )
            session.commit()

        analysis_store._ensure_schema()
        # Running the backfill again is safe and cannot duplicate rows.
        analysis_store._backfill_legacy_prediction_snapshots()

        with self.session_factory() as session:
            latest = session.get(models.Prediction, 12345)
            history = session.query(models.PredictionHistory).all()

        self.assertIsNotNone(latest)
        self.assertEqual(latest.home_probability, 48.0)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].source, "internal_legacy")
        self.assertEqual(history[0].model_version, "legacy-unknown")
        self.assertEqual(history[0].created_at, legacy_time)

    def test_manual_analysis_without_fixture_is_kept_in_history_only(self):
        analysis_store.save_analysis_snapshot(
            analysis_type="prédiction_manuelle",
            league_id=61,
            season=2026,
            home_team_id=85,
            away_team_id=86,
            fixture_id=None,
            prediction={
                "home_probability": 40.0,
                "draw_probability": 30.0,
                "away_probability": 30.0,
                "confidence": 40.0,
            },
        )

        with self.session_factory() as session:
            self.assertEqual(session.query(models.Prediction).count(), 0)
            history = session.query(models.PredictionHistory).one()

        self.assertIsNone(history.fixture_id)
        self.assertEqual(history.analysis_type, "prédiction_manuelle")
        self.assertEqual(history.source, "internal")

    def test_invalid_probability_distribution_is_not_persisted_as_prediction(self):
        analysis_store.save_analysis_snapshot(
            analysis_type="aperçu_match_à_venir",
            league_id=61,
            season=2026,
            home_team_id=85,
            away_team_id=86,
            fixture_id=12345,
            prediction={
                "home_probability": 80.0,
                "draw_probability": -5.0,
                "away_probability": 25.0,
            },
        )

        with self.session_factory() as session:
            self.assertEqual(session.query(models.Prediction).count(), 0)
            self.assertEqual(session.query(models.PredictionHistory).count(), 0)
            self.assertEqual(session.query(models.MatchAnalysisSnapshot).count(), 1)


if __name__ == "__main__":
    unittest.main()
