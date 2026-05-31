"""Per-run token + tool-call accounting (cross-cutting, M7 ablation).

A single `RunTrace` is stashed in a `contextvars.ContextVar` for the duration of
one ticket run. Two producers feed it, both no-ops when no trace is active (so
the production path is unchanged):

- `core/llm.py` attaches a tier-tagged callback to every chat model; the callback
  buckets `usage_metadata` (with an Ollama `prompt_eval_count`/`eval_count`
  fallback) by cost tier (`triage` | `vertical`). Tier — not model name — is the
  key, so cost routing is measurable even when both tiers map to the same local
  Ollama model.
- `core/executor.py` records every tool invocation (name, args, output, error).

The contextvar propagates across `await` boundaries within the same task, so it
captures nested sub-graph (`billing_graph`) and structured-output calls that
never surface in the message state.
"""

from __future__ import annotations

import contextvars
import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

Tier = str


@dataclass
class TierUsage:
    """Token totals for one cost tier within a single run."""

    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class ToolCallRecord:
    """One tool invocation observed at the `Executor` chokepoint."""

    tool: str
    args: dict[str, Any]
    output_text: str
    is_error: bool
    duration_ms: float


@dataclass
class RunTrace:
    """Mutable accumulator for a single ticket run."""

    usage_by_tier: dict[Tier, TierUsage] = field(default_factory=dict)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)

    def add_usage(self, tier: Tier, *, input_tokens: int, output_tokens: int) -> None:
        bucket = self.usage_by_tier.setdefault(tier, TierUsage())
        bucket.input_tokens += int(input_tokens)
        bucket.output_tokens += int(output_tokens)
        bucket.calls += 1

    def add_tool_call(self, record: ToolCallRecord) -> None:
        self.tool_calls.append(record)

    @property
    def total_input(self) -> int:
        return sum(b.input_tokens for b in self.usage_by_tier.values())

    @property
    def total_output(self) -> int:
        return sum(b.output_tokens for b in self.usage_by_tier.values())

    @property
    def total_tokens(self) -> int:
        return self.total_input + self.total_output

    @property
    def tool_names(self) -> list[str]:
        return [c.tool for c in self.tool_calls]

    @property
    def tool_error_count(self) -> int:
        return sum(1 for c in self.tool_calls if c.is_error)


_active_trace: contextvars.ContextVar[RunTrace | None] = contextvars.ContextVar(
    "resolveai_run_trace", default=None
)


def current_trace() -> RunTrace | None:
    return _active_trace.get()


@contextmanager
def capture_run() -> Iterator[RunTrace]:
    """Activate a fresh `RunTrace` for the enclosed (single-ticket) run."""
    trace = RunTrace()
    token = _active_trace.set(trace)
    try:
        yield trace
    finally:
        _active_trace.reset(token)


_ERROR_MARKERS = (
    "not_found",
    "already_refunded",
    "already_escalated",
    "invalid_refund_amount",
    "invalid_status",
    "requires_reason",
    '"error"',
    "error:",
    "exception",
    "traceback",
)


def looks_like_error(output: object) -> bool:
    text = output if isinstance(output, str) else json.dumps(output, default=str)
    lowered = text.lower()
    return any(marker in lowered for marker in _ERROR_MARKERS)


def record_tool_call(
    *,
    tool: str,
    args: dict[str, Any] | None,
    output: object,
    is_error: bool,
    duration_ms: float,
) -> None:
    trace = _active_trace.get()
    if trace is None:
        return
    text = output if isinstance(output, str) else json.dumps(output, default=str)
    trace.add_tool_call(
        ToolCallRecord(
            tool=tool,
            args=dict(args or {}),
            output_text=text[:2000],
            is_error=is_error,
            duration_ms=duration_ms,
        )
    )


def _usage_from_response(response: LLMResult) -> tuple[int, int]:
    input_tokens = 0
    output_tokens = 0
    for generation_list in getattr(response, "generations", []) or []:
        for generation in generation_list:
            message = getattr(generation, "message", None)
            if message is None:
                continue
            usage = getattr(message, "usage_metadata", None)
            if usage:
                input_tokens += int(usage.get("input_tokens", 0) or 0)
                output_tokens += int(usage.get("output_tokens", 0) or 0)
                continue
            # Ollama fallback: counts live in response_metadata.
            meta = getattr(message, "response_metadata", {}) or {}
            input_tokens += int(meta.get("prompt_eval_count", 0) or 0)
            output_tokens += int(meta.get("eval_count", 0) or 0)
    if input_tokens == 0 and output_tokens == 0:
        llm_output = getattr(response, "llm_output", None) or {}
        usage = llm_output.get("token_usage") or llm_output.get("usage") or {}
        if isinstance(usage, dict):
            input_tokens += int(
                usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0
            )
            output_tokens += int(
                usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0
            )
    return input_tokens, output_tokens


class TierUsageCallback(BaseCallbackHandler):
    """Buckets per-call token usage into the active `RunTrace` by cost tier."""

    # Run inline so the sync handler executes in the same async context and can
    # read the contextvar set by `capture_run()`.
    run_inline = True

    def __init__(self, tier: Tier) -> None:
        self.tier = tier

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        trace = _active_trace.get()
        if trace is None:
            return
        input_tokens, output_tokens = _usage_from_response(response)
        trace.add_usage(
            self.tier, input_tokens=input_tokens, output_tokens=output_tokens
        )


_TIER_CALLBACKS: dict[Tier, TierUsageCallback] = {}


def tier_callback(tier: Tier) -> TierUsageCallback:
    """Return a process-wide singleton callback for `tier` (cheap, stateless)."""
    callback = _TIER_CALLBACKS.get(tier)
    if callback is None:
        callback = TierUsageCallback(tier)
        _TIER_CALLBACKS[tier] = callback
    return callback
