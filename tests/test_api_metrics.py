import unittest

from components.api_metrics import (
    METRIC_DEFAULTS,
    format_api_metrics_captions,
    normalize_metrics_snapshot,
)
from services import api_football


class ApiMetricsFormattingTests(unittest.TestCase):
    def test_partial_snapshot_is_filled_with_safe_defaults(self):
        snapshot = normalize_metrics_snapshot({
            "requests_avoided": 12,
            "quota_remote_shared_tokens": 4,
        })
        self.assertEqual(snapshot["requests_avoided"], 12)
        self.assertEqual(snapshot["quota_remote_shared_tokens"], 4)
        self.assertEqual(snapshot["cache_hits"], 0)
        self.assertEqual(snapshot["quota_remote_block_denials"], 0)
        self.assertEqual(set(snapshot), set(METRIC_DEFAULTS))

    def test_none_and_non_mapping_snapshots_are_safe(self):
        self.assertEqual(normalize_metrics_snapshot(None), METRIC_DEFAULTS)
        self.assertEqual(normalize_metrics_snapshot("not a mapping"), METRIC_DEFAULTS)
        self.assertEqual(normalize_metrics_snapshot(42), METRIC_DEFAULTS)

    def test_mapping_subclass_with_broken_get_is_converted_to_plain_dict(self):
        class BrokenGetDict(dict):
            def get(self, key, default=None):
                if key == "quota_remote_shared_tokens":
                    raise KeyError(key)
                return super().get(key, default)

        snapshot = BrokenGetDict({"requests_avoided": 3})
        captions = format_api_metrics_captions(snapshot)
        self.assertEqual(len(captions), 3)
        self.assertIn("3 requêtes data évitées", captions[0])
        self.assertIn("0 tokens réservés", captions[2])

    def test_caption_formatter_handles_missing_shared_quota_metrics(self):
        captions = format_api_metrics_captions({
            "cache_hits": 8,
            "cache_entries": 2,
            "quota_status_checks": 6,
        })
        self.assertIn("0 requêtes data évitées", captions[0])
        self.assertIn("8 hits / 2 entrées", captions[0])
        self.assertIn("6 préflights / 0 blocages", captions[1])
        self.assertIn("0 tokens réservés / 0 refus", captions[2])

    def test_metrics_snapshot_contains_complete_schema_and_version(self):
        snapshot = api_football.cache_metrics_snapshot()
        for key in (
            "cache_hits",
            "cache_misses",
            "requests_avoided",
            "api_data_requests",
            "quota_status_checks",
            "quota_policy_blocks",
            "quota_atomic_reservations",
            "quota_atomic_blocks",
            "quota_ledger_errors",
            "quota_remote_shared_tokens",
            "quota_remote_block_denials",
            "quota_remote_fallbacks",
            "cache_evictions",
            "cache_entries",
            "schema_version",
        ):
            self.assertIn(key, snapshot)
        self.assertEqual(snapshot["schema_version"], api_football.CACHE_METRICS_SCHEMA_VERSION)


if __name__ == "__main__":
    unittest.main()
