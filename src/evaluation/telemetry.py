"""Per-query telemetry based on context variables."""

import contextvars
import time
from contextlib import contextmanager
from copy import deepcopy
from typing import Any, Optional


_collector: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar(
    "evaluation_telemetry",
    default=None,
)


def _empty_collector(run_id: str) -> dict:
    return {
        "run_id": run_id,
        "started_at": time.time(),
        "llm_calls": [],
        "node_timings_ms": {},
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
    }


@contextmanager
def telemetry_run(run_id: str):
    """Create an isolated telemetry collector for one query."""
    collector = _empty_collector(run_id)
    token = _collector.set(collector)
    try:
        yield collector
    finally:
        collector["elapsed_ms"] = round(
            (time.time() - collector["started_at"]) * 1000,
            2,
        )
        _collector.reset(token)


def current_collector() -> Optional[dict]:
    return _collector.get()


def snapshot() -> dict:
    collector = current_collector()
    return deepcopy(collector) if collector else {}


def _usage_from_response(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage_metadata", None) or {}
    metadata = getattr(response, "response_metadata", None) or {}
    token_usage = metadata.get("token_usage", {}) or metadata.get("usage_metadata", {}) or {}

    input_tokens = (
        usage.get("input_tokens")
        or usage.get("prompt_token_count")
        or token_usage.get("input_tokens")
        or token_usage.get("prompt_tokens")
        or token_usage.get("prompt_token_count")
        or 0
    )
    output_tokens = (
        usage.get("output_tokens")
        or usage.get("candidates_token_count")
        or token_usage.get("output_tokens")
        or token_usage.get("completion_tokens")
        or token_usage.get("candidates_token_count")
        or 0
    )
    total_tokens = (
        usage.get("total_tokens")
        or usage.get("total_token_count")
        or token_usage.get("total_tokens")
        or token_usage.get("total_token_count")
        or input_tokens + output_tokens
    )
    return {
        "input_tokens": int(input_tokens),
        "output_tokens": int(output_tokens),
        "total_tokens": int(total_tokens),
    }


def record_llm_call(
    *,
    provider: str,
    model: str,
    response: Any = None,
    elapsed_ms: float,
    label: str = "unknown",
    error: Optional[str] = None,
) -> None:
    collector = current_collector()
    if collector is None:
        return

    usage = _usage_from_response(response) if response is not None else {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
    }
    call = {
        "label": label,
        "provider": provider,
        "model": model,
        "elapsed_ms": round(elapsed_ms, 2),
        **usage,
        "error": error,
    }
    collector["llm_calls"].append(call)
    collector["input_tokens"] += usage["input_tokens"]
    collector["output_tokens"] += usage["output_tokens"]
    collector["total_tokens"] += usage["total_tokens"]


def record_node_timing(node_name: str, elapsed_ms: float) -> None:
    collector = current_collector()
    if collector is None:
        return
    timings = collector["node_timings_ms"].setdefault(node_name, [])
    timings.append(round(elapsed_ms, 2))


def timed_node(node_name: str, node_fn):
    """Wrap a synchronous LangGraph node without changing its return contract."""
    def wrapped(state):
        started = time.perf_counter()
        try:
            return node_fn(state)
        finally:
            record_node_timing(
                node_name,
                (time.perf_counter() - started) * 1000,
            )

    wrapped.__name__ = getattr(node_fn, "__name__", f"timed_{node_name}")
    return wrapped
