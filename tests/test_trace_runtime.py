import json
import unittest
from unittest.mock import patch

from services import trace_runtime


class FunctionTraceRuntimeTests(unittest.TestCase):
    def test_traces_function_entry_and_exit_with_correlation_id(self):
        namespace = {"__name__": "services.trace_test_module"}
        exec(
            "def traced_target(value):\n"
            "    return value + 1\n",
            namespace,
        )
        target = namespace["traced_target"]

        with patch.dict("os.environ", {"FOOTBALL_FUNCTION_TRACE": "1"}), patch.object(trace_runtime._LOGGER, "info") as log:
            trace_runtime.enable_function_tracing()
            try:
                self.assertEqual(target(4), 5)
            finally:
                trace_runtime.disable_function_tracing()

        records = [
            json.loads(call.args[1])
            for call in log.call_args_list
            if len(call.args) > 1 and isinstance(call.args[1], str)
        ]
        events = [record["event"] for record in records]
        self.assertIn("function_start", events)
        self.assertIn("function_end", events)
        traced = [record for record in records if record.get("function") == "traced_target"]
        self.assertTrue(traced)
        self.assertTrue(all(record.get("trace_id") for record in traced))
        self.assertTrue(all(record.get("span_id") for record in traced))

    def test_traces_exceptions_without_logging_secret_values(self):
        namespace = {"__name__": "services.trace_test_module"}
        exec(
            "def traced_failure(api_key):\n"
            "    raise ValueError('failure')\n",
            namespace,
        )
        target = namespace["traced_failure"]

        with patch.object(trace_runtime._LOGGER, "info") as log:
            trace_runtime.enable_function_tracing()
            try:
                with self.assertRaises(ValueError):
                    target("never-log-this")
            finally:
                trace_runtime.disable_function_tracing()

        records = [
            json.loads(call.args[1])
            for call in log.call_args_list
            if len(call.args) > 1 and isinstance(call.args[1], str)
        ]
        errors = [record for record in records if record["event"] == "function_exception"]
        self.assertTrue(errors)
        self.assertEqual(errors[-1]["error_type"], "ValueError")
        self.assertNotIn("never-log-this", json.dumps(records))
        start = next(record for record in records if record.get("function") == "traced_failure")
        self.assertIn("api_key", start["argument_names"])


if __name__ == "__main__":
    unittest.main()
