import json
import unittest
from unittest.mock import patch

from services import trace_runtime


class FunctionTraceRuntimeTests(unittest.TestCase):
    def test_traces_function_entry_and_exit_with_correlation_id(self):
        namespace = {"__name__": "services.trace_test_module"}
        exec(
            compile(
                "def traced_target(value):\n"
                "    return value + 1\n",
                trace_runtime._PROJECT_ROOT + "/services/trace_test_module.py",
                "exec",
            ),
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
        traced = [
            record for record in records
            if record.get("function") == "traced_target"
            and record.get("event") in {"function_start", "function_end", "function_exception"}
        ]
        self.assertTrue(traced)
        self.assertTrue(all(record.get("trace_id") for record in traced))
        self.assertTrue(all(record.get("span_id") for record in traced))

    def test_traces_exceptions_without_logging_secret_values(self):
        namespace = {"__name__": "services.trace_test_module"}
        exec(
            compile(
                "def traced_failure(api_key):\n"
                "    raise ValueError('failure')\n",
                trace_runtime._PROJECT_ROOT + "/services/trace_test_module.py",
                "exec",
            ),
            namespace,
        )
        target = namespace["traced_failure"]

        with patch.dict("os.environ", {"FOOTBALL_FUNCTION_TRACE": "1"}), patch.object(trace_runtime._LOGGER, "info") as log:
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


    def test_caps_repeated_function_spans_and_reports_suppressed_calls(self):
        namespace = {"__name__": "services.trace_test_module"}
        exec(
            compile(
                "def traced_repeated(value):\n"
                "    return value + 1\n",
                trace_runtime._PROJECT_ROOT + "/services/trace_test_module.py",
                "exec",
            ),
            namespace,
        )
        target = namespace["traced_repeated"]

        with patch.dict(
            "os.environ",
            {
                "FOOTBALL_FUNCTION_TRACE": "1",
                "FOOTBALL_FUNCTION_TRACE_MAX_SPANS_PER_FUNCTION": "2",
            },
        ), patch.object(trace_runtime._LOGGER, "info") as log:
            trace_runtime.enable_function_tracing()
            try:
                self.assertEqual([target(value) for value in range(5)], [1, 2, 3, 4, 5])
            finally:
                trace_runtime.disable_function_tracing()

        records = [
            json.loads(call.args[1])
            for call in log.call_args_list
            if len(call.args) > 1 and isinstance(call.args[1], str)
        ]
        starts = [
            record for record in records
            if record.get("event") == "function_start"
            and record.get("function") == "traced_repeated"
        ]
        ends = [
            record for record in records
            if record.get("event") == "function_end"
            and record.get("function") == "traced_repeated"
        ]
        summary = next(
            record for record in records
            if record.get("event") == "function_trace_summary"
            and record.get("function") == "traced_repeated"
        )
        self.assertEqual(len(starts), 2)
        self.assertEqual(len(ends), 2)
        self.assertEqual(summary["calls"], 5)
        self.assertEqual(summary["sampled_calls"], 2)
        self.assertEqual(summary["suppressed_calls"], 3)
        self.assertEqual(summary["completed_calls"], 5)

    def test_sampled_trace_counts_exceptions_from_suppressed_calls(self):
        namespace = {"__name__": "services.trace_test_module"}
        exec(
            compile(
                "def traced_repeated_failure(value):\n"
                "    raise ValueError('failure')\n",
                trace_runtime._PROJECT_ROOT + "/services/trace_test_module.py",
                "exec",
            ),
            namespace,
        )
        target = namespace["traced_repeated_failure"]

        with patch.dict(
            "os.environ",
            {
                "FOOTBALL_FUNCTION_TRACE": "1",
                "FOOTBALL_FUNCTION_TRACE_MAX_SPANS_PER_FUNCTION": "1",
            },
        ), patch.object(trace_runtime._LOGGER, "info") as log:
            trace_runtime.enable_function_tracing()
            try:
                for value in range(4):
                    with self.assertRaises(ValueError):
                        target(value)
            finally:
                trace_runtime.disable_function_tracing()

        records = [
            json.loads(call.args[1])
            for call in log.call_args_list
            if len(call.args) > 1 and isinstance(call.args[1], str)
        ]
        errors = [
            record for record in records
            if record.get("event") == "function_exception"
            and record.get("function") == "traced_repeated_failure"
        ]
        summary = next(
            record for record in records
            if record.get("event") == "function_trace_summary"
            and record.get("function") == "traced_repeated_failure"
        )
        self.assertEqual(len(errors), 1)
        self.assertEqual(summary["calls"], 4)
        self.assertEqual(summary["sampled_calls"], 1)
        self.assertEqual(summary["suppressed_calls"], 3)
        self.assertEqual(summary["exception_calls"], 4)


if __name__ == "__main__":
    unittest.main()
