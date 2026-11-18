import datetime
import unittest
from unittest.mock import patch

import pandas as pd
from sqlalchemy import create_engine

from services import prediction_archive_service


class PredictionArchiveServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        with self.engine.begin() as connection:
            connection.exec_driver_sql(
                """
                CREATE TABLE teams (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL
                )
                """
            )
            connection.exec_driver_sql(
                """
                CREATE TABLE matches (
                    fixture_id INTEGER PRIMARY KEY,
                    league_id INTEGER NOT NULL,
                    season INTEGER NOT NULL,
                    date TEXT NOT NULL,
                    home_team_id INTEGER NOT NULL,
                    away_team_id INTEGER NOT NULL,
                    home_goals INTEGER,
                    away_goals INTEGER
                )
                """
            )
            connection.exec_driver_sql(
                "INSERT INTO teams (id, name) VALUES (1, 'Domicile'), (2, 'Extérieur')"
            )
            connection.exec_driver_sql(
                """
                INSERT INTO matches (
                    fixture_id, league_id, season, date, home_team_id,
                    away_team_id, home_goals, away_goals
                ) VALUES
                    (100, 61, 2026, '2026-11-19 19:00:00', 1, 2, NULL, NULL),
                    (101, 61, 2026, '2026-11-18 11:00:00', 1, 2, NULL, NULL),
                    (102, 61, 2026, '2026-11-20 19:00:00', 1, 2, 1, 0)
                """
            )

    def tearDown(self):
        self.engine.dispose()

    @staticmethod
    def _final_prediction():
        return {
            "prediction": {
                "home_probability": 45.0,
                "draw_probability": 28.0,
                "away_probability": 27.0,
                "confidence": 45.0,
            },
            "score_prediction": {
                "matrix": [
                    {
                        "Score": "1-1",
                        "Buts domicile": 1,
                        "Buts extérieur": 1,
                        "Probabilité": 28.0,
                    }
                ]
            },
            "model_details": {"draw_calibration": {"applied": True}},
        }

    def test_archives_only_future_fixtures_with_enough_history(self):
        now = datetime.datetime(2026, 11, 18, 12, tzinfo=datetime.UTC)
        history = pd.DataFrame([{"fixture_id": value} for value in range(30)])
        with (
            patch.object(
                prediction_archive_service.prediction_helpers,
                "load_historical_context",
                return_value=history,
            ) as load_history,
            patch.object(
                prediction_archive_service.final_prediction_service,
                "calculate",
                return_value=self._final_prediction(),
            ) as calculate,
            patch.object(
                prediction_archive_service.analysis_store,
                "save_analysis_snapshot",
            ) as save,
        ):
            result = prediction_archive_service.archive_upcoming_predictions(
                now=now,
                days=14,
                min_history_matches=30,
                db_engine=self.engine,
            )

        self.assertEqual(result["fixtures_considered"], 1)
        self.assertEqual(result["archived"], 1)
        self.assertEqual(result["skipped_started"], 0)
        self.assertEqual(result["api_calls"], 0)
        load_history.assert_called_once_with(61, "2026-11-19T19:00:00+00:00")
        calculate.assert_called_once()
        self.assertEqual(calculate.call_args.kwargs["match_date"], "2026-11-19T19:00:00+00:00")
        save.assert_called_once()
        self.assertEqual(
            save.call_args.kwargs["analysis_type"],
            "prédiction_automatique_pré-match",
        )
        self.assertEqual(save.call_args.kwargs["fixture_id"], 100)

    def test_skips_fixture_when_historical_context_is_insufficient(self):
        history = pd.DataFrame([{"fixture_id": 1} for _ in range(29)])
        with (
            patch.object(
                prediction_archive_service.prediction_helpers,
                "load_historical_context",
                return_value=history,
            ),
            patch.object(
                prediction_archive_service.final_prediction_service,
                "calculate",
            ) as calculate,
            patch.object(
                prediction_archive_service.analysis_store,
                "save_analysis_snapshot",
            ) as save,
        ):
            result = prediction_archive_service.archive_upcoming_predictions(
                now=datetime.datetime(2026, 11, 18, 12, tzinfo=datetime.UTC),
                min_history_matches=30,
                db_engine=self.engine,
            )

        self.assertEqual(result["archived"], 0)
        self.assertEqual(result["skipped_insufficient_history"], 1)
        calculate.assert_not_called()
        save.assert_not_called()

    def test_invalid_probability_distribution_is_not_saved(self):
        invalid = self._final_prediction()
        invalid["prediction"]["draw_probability"] = 40.0
        history = pd.DataFrame([{"fixture_id": value} for value in range(30)])
        with (
            patch.object(
                prediction_archive_service.prediction_helpers,
                "load_historical_context",
                return_value=history,
            ),
            patch.object(
                prediction_archive_service.final_prediction_service,
                "calculate",
                return_value=invalid,
            ),
            patch.object(
                prediction_archive_service.analysis_store,
                "save_analysis_snapshot",
            ) as save,
        ):
            result = prediction_archive_service.archive_upcoming_predictions(
                now=datetime.datetime(2026, 11, 18, 12, tzinfo=datetime.UTC),
                db_engine=self.engine,
            )

        self.assertEqual(result["archived"], 0)
        self.assertEqual(len(result["errors"]), 1)
        save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
