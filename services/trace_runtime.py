"""Low-overhead structured function tracing for application diagnostics.

Only function metadata and an allowlist of operational identifiers are emitted.
Argument values, credentials, request bodies, headers, and user data are excluded.
"""
from __future__ import annotations

import contextlib
import contextvars
import datetime
import json
import logging
import os
import re
import sys
import threading
import time
import uuid

_LOGGER = logging.getLogger("football.trace")
_TRACE_ID = contextvars.ContextVar("football_trace_id", default=None)
_THREAD = threading.local()
_ENABLED = False
_PREVIOUS_PROFILE = None
_PREVIOUS_THREAD_PROFILE = None
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))\n_TEST_ROOT = os.path.join(_PROJECT_ROOT, "tests")
_SECRET_VALUE = re.compile(r"(?i)(gh[pousr]_[A-Za-z0-9_]{20,}|Bearer\s+[^\s]+)")
_SECRET_KEY = re.compile(r"(?i)(token|secret|password|authorization|cookie|api.?key|credential|headers?)")
_SAFE_CONTEXT_NAMES = {
    "fixture_id", "league_id", "season", "resource_key", "resource_type",
    "retry_hours", "page", "sync_run_id", "current", "total", "label",
    "path", "endpoint", "attempt", "status", "priority", "cost",
    "days_ahead", "owner", "scope_key", "batch_key",
}
_SAFE_PARAM_NAMES = {
    "league", "season", "fixture", "team", "date", "from", "to", "id",
    "live", "type", "page", "last", "timezone", "player", "coach",
}


def _utc_now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="milliseconds")


def _safe_error(value) -> str:
    message = str(value or "")[:300]
    message = _SECRET_VALUE.sub("[REDACTED]", message)
    message = re.sub(r"(?i)([?&](?:key|token|api_key|authorization)=)[^&\s]+", r"\1[REDACTED]", message)
    return message


def _safe_value(value):
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _SECRET_VALUE.sub("[REDACTED]", value[:300])
    if isinstance(value, (list, tuple)):
        return [_safe_value(item) for item in value[:32] if item is None or isinstance(item, (bool, int, float, str))]
    if isinstance(value, dict):
        result = {}
        for key, item in list(value.items())[:32]:
            key = str(key)
            if _SECRET_KEY.search(key):
                result[key] = "[REDACTED]"
            elif item is None or isinstance(item, (bool, int, float, str)):
                result[key] = _safe_value(item)
        return result
    return type(value).__name__


def _safe_fields(fields: dict) -> dict:
    safe = {}
    for key, value in (fields or {}).items():
        if _SECRET_KEY.search(str(key)):
            safe[str(key)] = "[REDACTED]"
        else:
            safe[str(key)] = _safe_value(value)
    return safe


def _function_context(frame) -> dict:
    local_values = frame.f_locals
    context = {
        name: local_values[name]
        for name in _SAFE_CONTEXT_NAMES
        if name in local_values
        and (local_values[name] is None or isinstance(local_values[name], (bool, int, float, str)))
    }
    params = local_values.get("params")
    if isinstance(params, dict):
        context["request_params"] = {
            str(key): value
            for key, value in params.items()
            if str(key).lower() in _SAFE_PARAM_NAMES
            and (value is None or isinstance(value, (bool, int, float, str)))
        }
    return context


def current_trace_id() -> str | None:
    return _TRACE_ID.get()


def _emit(event: str, **fields) -> None:
    record = {
        "timestamp": _utc_now(),
        "event": event,
        "trace_id": _TRACE_ID.get(),
        "process_id": os.getpid(),
        "thread": threading.current_thread().name,
        **_safe_fields(fields),
    }
    try:
        _LOGGER.info("%s", json.dumps(record, ensure_ascii=False, sort_keys=True))
    except Exception:
        # Diagnostics must never change application behavior.
        pass


def trace_event(event: str, **fields) -> None:
    """Emit a safe structured event inside the current trace."""
    _emit(str(event)[:80], **fields)


@contextlib.contextmanager
def trace_scope(operation: str, *, trace_id: str | None = None, **fields):
    """Give a multi-function operation one correlation ID."""
    token = _TRACE_ID.set(trace_id or _TRACE_ID.get() or uuid.uuid4().hex)
    started = time.perf_counter()
    _emit("operation_start", operation=operation, **fields)
    try:
        yield _TRACE_ID.get()
    except BaseException as exc:
        _emit(
            "operation_error",
            operation=operation,
            duration_ms=round((time.perf_counter() - started) * 1000, 3),
            error_type=type(exc).__name__,
            error=_safe_error(exc),
        )
        raise
    else:
        _emit(
            "operation_end",
            operation=operation,
            duration_ms=round((time.perf_counter() - started) * 1000, 3),
            status="ok",
        )
    finally:
        _TRACE_ID.reset(token)


def _is_project_frame(frame) -> bool:
    module = str(frame.f_globals.get("__name__", ""))
    filename = os.path.realpath(frame.f_code.co_filename)
    inside_project = filename.startswith(_PROJECT_ROOT + os.sep)
    inside_tests = filename.startswith(_TEST_ROOT + os.sep)
    return inside_project and not inside_tests and module != __name__


def _find_entry(stack: list, frame):
    for index in range(len(stack) - 1, -1, -1):
        if stack[index]["frame"] is frame:
            return index, stack[index]
    return None, None


def _profile(frame, event, arg):
    # Preserve an existing profiler rather than silently disabling it.
    previous = _PREVIOUS_PROFILE
    if previous is not None and previous is not _profile:
        try:
            previous(frame, event, arg)
        except Exception:
            pass

    if not _is_project_frame(frame):
        return

    stack = getattr(_THREAD, "stack", None)
    if stack is None:
        stack = []
        _THREAD.stack = stack

    if event == "call":
        trace_token = None
        if not stack and _TRACE_ID.get() is None:
            trace_token = _TRACE_ID.set(uuid.uuid4().hex)
        parent = stack[-1]["span_id"] if stack else None
        code = frame.f_code
        entry = {
            "frame": frame,
            "span_id": uuid.uuid4().hex[:16],
            "parent_span_id": parent,
            "started": time.perf_counter(),
            "trace_token": trace_token,
            "exception_seen": False,
            "module": str(frame.f_globals.get("__name__", "")),
            "function": code.co_qualname,
            "file": os.path.basename(code.co_filename),
            "line": code.co_firstlineno,
        }
        stack.append(entry)
        _emit(
            "function_start",
            span_id=entry["span_id"],
            parent_span_id=parent,
            module=entry["module"],
            function=entry["function"],
            file=entry["file"],
            line=entry["line"],
            argument_names=list(code.co_varnames[:code.co_argcount + code.co_kwonlyargcount]),
            context=_function_context(frame),
            depth=len(stack),
        )
        return

    index, entry = _find_entry(stack, frame)
    if entry is None:
        return

    if event == "exception":
        exception_type, exception, _traceback = arg
        entry["exception_seen"] = True
        _emit(
            "function_exception",
            span_id=entry["span_id"],
            parent_span_id=entry["parent_span_id"],
            module=entry["module"],
            function=entry["function"],
            file=entry["file"],
            line=frame.f_lineno,
            error_type=getattr(exception_type, "__name__", str(exception_type)),
            error=_safe_error(exception),
        )
        return

    if event == "return":
        stack.pop(index)
        _emit(
            "function_end",
            span_id=entry["span_id"],
            parent_span_id=entry["parent_span_id"],
            module=entry["module"],
            function=entry["function"],
            file=entry["file"],
            line=entry["line"],
            duration_ms=round((time.perf_counter() - entry["started"]) * 1000, 3),
            status="exception_seen" if entry["exception_seen"] else "ok",
            depth=len(stack),
        )
        if entry["trace_token"] is not None:
            _TRACE_ID.reset(entry["trace_token"])


def enable_function_tracing() -> bool:
    """Trace calls in project packages on this thread and future threads."""
    global _ENABLED, _PREVIOUS_PROFILE, _PREVIOUS_THREAD_PROFILE
    if _ENABLED:
        return True
    if os.getenv("FOOTBALL_FUNCTION_TRACE", "").strip().lower() not in {"1", "true", "yes", "on"}:
        return False
    _PREVIOUS_PROFILE = sys.getprofile()
    _PREVIOUS_THREAD_PROFILE = threading.getprofile()
    sys.setprofile(_profile)
    threading.setprofile(_profile)
    _ENABLED = True
    _emit(
        "tracing_enabled",
        modules="services,scripts,database,pages,components",
        values_logged=False,
        argument_values_logged=False,
    )
    return True


def disable_function_tracing() -> None:
    """Disable tracing and restore any profiler that was active beforehand."""
    global _ENABLED
    if not _ENABLED:
        return
    sys.setprofile(_PREVIOUS_PROFILE)
    threading.setprofile(_PREVIOUS_THREAD_PROFILE)
    _ENABLED = False
