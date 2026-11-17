import datetime
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import models
from services import backtest_service


class PersistedPredictionEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.test_engine = create_engine("sqlite://")
        models.Base.metadata.create_all(self.test_engine)
        self.session_factory = sessionmaker(bind=self.test_engine)

    def tearDown(self):
        self.test_engine.dispose()

    def _match(self, fixture_id, kickoff, home_goals, away_goals):
        return models.Match(
            fixture_id=fixture_id,
            league_id=61,
            season=2026,
            date=kickoff,
            home_team_id=100 + fixture_id * 2,
            away_team_id=101 + fixture_id * 2,
            home_goals=home_goals,
            away_goals=away_goals,
            status="Match Finished",
        )

    def _prediction(
        self, fixture_id, created_at, probabilities, *,
        source="internal", model_version="engine-v1",
    ):
        return models.PredictionHistory(
            prediction_key=f"{fixture_id}-{created_at.isoformat()}-{source}-{model_version}",
            fixture_id=fixture_id,
            league_id=61,
            season=2026,
            home_team_id=100 + fixture_id * 2,
            away_team_id=101 + fixture_id * 2,
            home_probability=probabilities[0],
            draw_probability=probabilities[1],
            away_probability=probabilities[2],
            model_version=model_version,
            source=source,
            analysis_type="test",
            created_at=created_at,
        )

    def test_uses_latest_pre_kickoff_internal_forecast_only(self):
        kickoff = datetime.datetime(2026, 11, 20, 18, 0)
        with self.session_factory() as session:
            session.add_all([
                self._match(1, kickoff, 2, 0),
                self._match(2, kickoff, 1, 1),
                self._match(3, kickoff, 0, 1),
                self._prediction(
                    1, kickoff - datetime.timedelta(hours=2), [55, 25, 20]
                ),
                self._prediction(
                    1, kickoff - datetime.timedelta(minutes=30), [65, 20, 15]
                ),
                self._prediction(
                    2, kickoff + datetime.timedelta(minutes=1), [20, 60, 20]
                ),
                self._prediction(
                    3, kickoff - datetime.timedelta(hours=1), [20, 25, 55],
                    source="internal_legacy", model_version="legacy-unknown",
                ),
                self._prediction(
                    1, kickoff - datetime.timedelta(hours=1), [99, 0, 1],
                    source="api", model_version="api-v1",
                ),
            ])
            session.commit()

        result = backtest_service.evaluate_persisted_predictions(self.test_engine)

        self.assertEqual(result["matches_evaluated"], 2)
        self.assertEqual(result["post_kickoff_predictions_excluded"], 1)
        self.assertEqual(result["invalid_predictions_excluded"], 0)
        groups = {
            (item["source"], item["model_version"]): item["metrics"]["matches"]
            for item in result["groups"]
        }
        self.assertEqual(groups[("internal", "engine-v1")], 1)
        self.assertEqual(groups[("internal_legacy", "legacy-unknown")], 1)

    def test_invalid_distribution_is_excluded_from_metrics(self):
        kickoff = datetime.datetime(2026, 11, 20, 18, 0)
        with self.session_factory() as session:
            session.add(self._match(4, kickoff, 1, 0))
            session.add(
                self._prediction(
                    4, kickoff - datetime.timedelta(hours=1), [70, 20, 20]
                )
            )
            session.commit()

        result = backtest_service.evaluate_persisted_predictions(self.test_engine)
        self.assertEqual(result["matches_evaluated"], 0)
        self.assertEqual(result["invalid_predictions_excluded"], 1)


if __name__ == "__main__":
    unittest.main()
