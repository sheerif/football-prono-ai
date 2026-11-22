import unittest

from services.download_intelligence import (
    annotate_resource_rows,
    order_download_tasks,
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


if __name__ == "__main__":
    unittest.main()
