import unittest

from services.download_intelligence import (
    annotate_resource_rows,
    order_dispatch_phases,
    order_download_tasks,
    order_season_player_scopes,
    task_from_resource_row,
)


class DownloadIntelligenceTests(unittest.TestCase):
    def test_completed_resource_is_not_actionable(self):
        row = {"key": "fixture-detail", "missing": 0, "deferred": 0}
        self.assertEqual(order_download_tasks([row]), [])

    def test_deferred_missing_items_are_not_actionable(self):
        row = {"key": "fixture-statistics", "missing": 4, "deferred": 4}
        self.assertEqual(order_download_tasks([row]), [])

    def test_actionable_missing_count_excludes_deferred_items(self):
        task = task_from_resource_row(
            {"key": "fixture-statistics", "missing": 7, "deferred": 3}
        )
        self.assertEqual(task.missing - task.deferred, 4)
        self.assertIn("4 élément(s)", task.reason)

    def test_priority_and_urgency_order_are_deterministic(self):
        rows = [
            {"key": "fixture-statistics", "missing": 2},
            {"key": "season-players", "missing": 2},
            {"key": "fixture-prediction", "missing": 2},
            {"key": "fixture-detail", "missing": 2, "urgency": 5},
        ]
        ordered = order_download_tasks(rows)
        self.assertEqual(
            [item["key"] for item in ordered],
            [
                "fixture-prediction",
                "fixture-detail",
                "season-players",
                "fixture-statistics",
            ],
        )

    def test_annotated_plan_recommends_only_first_actionable_resource(self):
        rows = [
            {"key": "core", "missing": 0, "deferred": 0},
            {"key": "fixture-prediction", "missing": 3, "deferred": 1},
            {"key": "fixture-statistics", "missing": 5, "deferred": 5},
        ]
        plan = annotate_resource_rows(rows)
        prediction = next(item for item in plan if item["key"] == "fixture-prediction")
        xg = next(item for item in plan if item["key"] == "fixture-statistics")
        core = next(item for item in plan if item["key"] == "core")
        self.assertTrue(prediction["recommended"])
        self.assertEqual(prediction["actionable_missing"], 2)
        self.assertFalse(xg["recommended"])
        self.assertEqual(xg["actionable_missing"], 0)
        self.assertIsNone(core["plan_rank"])


    def test_sql_plan_controls_real_dispatch_phase_order_with_fallback(self):
        plan = {
            "priority_order": [
                {"key": "fixture-lineup", "priority": 5},
                {"key": "fixture-detail", "priority": 15},
                {"key": "fixture-prediction", "priority": 25},
            ]
        }
        phases = ["fixture-prediction", "fixture-detail", "fixture-lineup"]
        self.assertEqual(
            order_dispatch_phases(
                plan,
                phases,
                fallback_order=phases,
            ),
            ["fixture-lineup", "fixture-detail", "fixture-prediction"],
        )

    def test_missing_plan_entries_use_stable_fallback_after_ranked_phases(self):
        fallback = ["fixture-prediction", "fixture-detail", "fixture-lineup"]
        self.assertEqual(
            order_dispatch_phases(
                {"priority_order": [{"key": "fixture-detail", "priority": 20}]},
                fallback,
                fallback_order=fallback,
            ),
            ["fixture-detail", "fixture-prediction", "fixture-lineup"],
        )

    def test_season_player_scopes_prioritize_recent_empty_and_larger_scopes(self):
        scopes = [
            {"league_id": 2, "season": 2024, "match_count": 10, "player_count": 3},
            {"league_id": 1, "season": 2025, "match_count": 4, "player_count": 0},
            {"league_id": 3, "season": 2025, "match_count": 8, "player_count": 0},
            {"league_id": 4, "season": 2023, "match_count": 100, "player_count": 0},
            {"league_id": 5, "season": 2025, "match_count": 9, "player_count": 2},
        ]
        ordered = order_season_player_scopes(scopes, recent_seasons={2025})
        self.assertEqual(
            [(item["league_id"], item["season"]) for item in ordered],
            [(3, 2025), (1, 2025), (5, 2025), (4, 2023), (2, 2024)],
        )
        self.assertEqual(scopes[0]["league_id"], 2)  # input is not mutated


if __name__ == "__main__":
    unittest.main()
