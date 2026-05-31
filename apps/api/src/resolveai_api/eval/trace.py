"""Eval-facing trace surface + tool-error classification (M7).

Re-exports the cross-cutting accounting primitives from `core.usage` and adds
the tool-error rule the ablation harness scores against:

A ticket has a *tool error* if any of the following holds:
- a tool the model called raised / returned an error payload (`is_error`);
- the model called a tool that is not in the ticket's `expected_tool_calls`
  (wrong-tool selection), when an expectation is provided;
- the output guardrails flagged a hallucinated entity / doc id for the run.
"""

from __future__ import annotations

from collections.abc import Iterable

from resolveai_api.core.usage import (
    RunTrace,
    TierUsage,
    ToolCallRecord,
    capture_run,
    current_trace,
    looks_like_error,
    record_tool_call,
    tier_callback,
)

__all__ = [
    "RunTrace",
    "TierUsage",
    "ToolCallRecord",
    "ToolErrorReport",
    "capture_run",
    "classify_tool_errors",
    "current_trace",
    "looks_like_error",
    "record_tool_call",
    "tier_callback",
]

_HALLUCINATION_PREFIXES = ("hallucinated:", "grounding:hallucinated")


class ToolErrorReport:
    """Per-ticket tool-error breakdown (booleans + reasons)."""

    def __init__(
        self,
        *,
        failed_calls: list[str],
        wrong_tools: list[str],
        hallucinations: list[str],
    ) -> None:
        self.failed_calls = failed_calls
        self.wrong_tools = wrong_tools
        self.hallucinations = hallucinations

    @property
    def has_error(self) -> bool:
        return bool(self.failed_calls or self.wrong_tools or self.hallucinations)

    def reasons(self) -> list[str]:
        reasons: list[str] = []
        reasons.extend(f"failed:{name}" for name in self.failed_calls)
        reasons.extend(f"wrong_tool:{name}" for name in self.wrong_tools)
        reasons.extend(self.hallucinations)
        return reasons

    def as_dict(self) -> dict[str, object]:
        return {
            "has_error": self.has_error,
            "failed_calls": self.failed_calls,
            "wrong_tools": self.wrong_tools,
            "hallucinations": self.hallucinations,
        }


def classify_tool_errors(
    *,
    trace: RunTrace,
    expected_tool_calls: Iterable[str] | None,
    flags: Iterable[str],
) -> ToolErrorReport:
    failed_calls = [c.tool for c in trace.tool_calls if c.is_error]

    wrong_tools: list[str] = []
    expected = {name for name in (expected_tool_calls or []) if name}
    if expected:
        wrong_tools = [
            c.tool
            for c in trace.tool_calls
            if c.tool not in expected and not c.is_error
        ]

    hallucinations = [
        flag
        for flag in flags
        if any(flag.startswith(prefix) for prefix in _HALLUCINATION_PREFIXES)
    ]
    return ToolErrorReport(
        failed_calls=failed_calls,
        wrong_tools=wrong_tools,
        hallucinations=hallucinations,
    )
