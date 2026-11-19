import unittest
from unittest.mock import patch

import pandas as pd

from pages import dashboard


class DashboardKpiErrorTests(unittest.TestCase):
    def test_formatters_keep_unknown_distinct_from_zero(self):
        self.assertEqual(dashboard._format_int(None), "Indisponible")
        self.assertEqual(dashboard._format_int(0), "0")
        self.assertEqual(dashboard._format_percent(None), "Indisponible")
        self.assertEqual(dashboard._format_percent(0), "0.0 %")

    def test_failed_match_read_is_marked_not_silently_empty(self):
        dashboard._load_matches.clear()
        with patch.object(pd, "read_sql", side_effect=RuntimeError("database unavailable")):
            result = dashboard._load_matches()
        self.assertTrue(result.empty)
        self.assertIn("database unavailable", result.attrs["load_error"])
        dashboard._load_matches.clear()

    def test_health_query_failures_return_unknown_and_error(self):
        class BrokenEngine:
            def begin(self):
                raise RuntimeError("connection failed")

        with patch.object(dashboard, "engine", BrokenEngine()):
            dashboard._load_data_health.clear()
            result = dashboard._load_data_health()
        self.assertIsNone(result["upcoming"])
        self.assertIn("connection failed", result["errors"]["database"])
        dashboard._load_data_health.clear()

    def test_next_action_explains_data_read_failure(self):
        matches = pd.DataFrame(columns=dashboard.MATCH_COLUMNS)
        matches.attrs["load_error"] = "SQL error"
        self.assertIn("vérifier", dashboard._next_action({"errors": {}}, matches).lower())

    def test_quick_summary_reports_unavailable(self):
        self.assertIn("indisponibles", dashboard._quick_read_sentence({"completed_count": None}))
