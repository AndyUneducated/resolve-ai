"""Unit + smoke tests for the M7 architecture-ablation eval library."""

from __future__ import annotations

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from langgraph.checkpoint.memory import MemorySaver
from resolveai_api.agents.supervisor import GraphOptions, SupervisorGraph
from resolveai_api.core.usage import (
    RunTrace,
    TierUsage,
    TierUsageCallback,
    ToolCallRecord,
    _usage_from_response,
    capture_run,
)
from resolveai_api.eval import arch_scoring, pricing
from resolveai_api.eval.judge import ResolutionJudge, ResolutionVerdict
from resolveai_api.eval.trace import classify_tool_errors
from resolveai_api.eval.variants import VARIANTS, build_variant
from resolveai_api.mcp.toolbelt import ToolBelt


def _llm_result(*, usage=None, response_metadata=None) -> LLMResult:
    if usage is not None and "total_tokens" not in usage:
        usage = {
            **usage,
            "total_tokens": int(usage.get("input_tokens", 0))
            + int(usage.get("output_tokens", 0)),
        }
    message = AIMessage(
        content="ok",
        usage_metadata=usage,
        response_metadata=response_metadata or {},
    )
    return LLMResult(generations=[[ChatGeneration(message=message)]])


# --------------------------------------------------------------------------- #
# pricing
# --------------------------------------------------------------------------- #


def test_cost_routing_makes_triage_cheaper_than_vertical() -> None:
    usage = TierUsage(input_tokens=1_000_000, output_tokens=1_000_000)
    triage_cost = pricing.usage_cost_usd("triage", usage)
    vertical_cost = pricing.usage_cost_usd("vertical", usage)
    # haiku (0.80/4.00) vs sonnet (3.00/15.00) for 1M in + 1M out
    assert triage_cost == 0.80 + 4.00
    assert vertical_cost == 3.00 + 15.00
    assert triage_cost < vertical_cost


def test_cost_usd_sums_tiers() -> None:
    usage_by_tier = {
        "triage": TierUsage(input_tokens=500_000, output_tokens=0),
        "vertical": TierUsage(input_tokens=0, output_tokens=1_000_000),
    }
    expected = 0.5 * 0.80 + 1.0 * 15.00
    assert abs(pricing.cost_usd(usage_by_tier) - expected) < 1e-9


# --------------------------------------------------------------------------- #
# usage capture
# --------------------------------------------------------------------------- #


def test_tier_usage_callback_buckets_into_active_trace() -> None:
    callback = TierUsageCallback("triage")
    with capture_run() as trace:
        callback.on_llm_end(
            _llm_result(
                usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
            )
        )
        callback.on_llm_end(
            _llm_result(
                usage={"input_tokens": 4, "output_tokens": 1, "total_tokens": 5}
            )
        )
    assert trace.usage_by_tier["triage"].input_tokens == 14
    assert trace.usage_by_tier["triage"].output_tokens == 6
    assert trace.total_tokens == 20


def test_callback_is_noop_without_active_trace() -> None:
    callback = TierUsageCallback("vertical")
    # Should not raise when no capture_run context is active.
    callback.on_llm_end(_llm_result(usage={"input_tokens": 9, "output_tokens": 9}))


def test_usage_from_ollama_response_metadata_fallback() -> None:
    response = _llm_result(
        usage=None, response_metadata={"prompt_eval_count": 42, "eval_count": 7}
    )
    input_tokens, output_tokens = _usage_from_response(response)
    assert input_tokens == 42
    assert output_tokens == 7


# --------------------------------------------------------------------------- #
# tool-error classification
# --------------------------------------------------------------------------- #


def test_classify_tool_errors_detects_all_three_kinds() -> None:
    trace = RunTrace()
    trace.tool_calls = [
        ToolCallRecord("stripe.refund", {}, "already_refunded", is_error=True, duration_ms=1.0),
        ToolCallRecord("slack.notify_team", {}, "{}", is_error=False, duration_ms=1.0),
        ToolCallRecord("stripe.get_charge", {}, "{}", is_error=False, duration_ms=1.0),
    ]
    report = classify_tool_errors(
        trace=trace,
        expected_tool_calls=["stripe.get_charge", "stripe.refund"],
        flags=["hallucinated:ch_999", "grounding:no_kb_context"],
    )
    assert report.has_error
    assert "stripe.refund" in report.failed_calls
    assert "slack.notify_team" in report.wrong_tools  # not in expected, not an error
    assert "stripe.get_charge" not in report.wrong_tools  # expected -> ok
    assert "hallucinated:ch_999" in report.hallucinations


def test_classify_tool_errors_clean_when_all_expected() -> None:
    trace = RunTrace()
    trace.tool_calls = [
        ToolCallRecord("stripe.get_charge", {}, "{}", is_error=False, duration_ms=1.0)
    ]
    report = classify_tool_errors(
        trace=trace, expected_tool_calls=["stripe.get_charge"], flags=[]
    )
    assert not report.has_error


# --------------------------------------------------------------------------- #
# scoring
# --------------------------------------------------------------------------- #


def test_percentile_interpolates() -> None:
    values = [10.0, 20.0, 30.0, 40.0]
    assert arch_scoring._percentile(values, 50) == 25.0
    assert arch_scoring._percentile([5.0], 95) == 5.0
    assert arch_scoring._percentile([], 50) == 0.0


def _row(variant: str, **kw) -> dict:
    base = {
        "variant": variant,
        "outcome": "ok",
        "input_tokens": 100,
        "output_tokens": 50,
        "total_tokens": 150,
        "cost_usd": 0.001,
        "latency_ms": 1000.0,
        "resolved": True,
        "score": 1.0,
        "tool_error": False,
        "category": "billing",
        "id": f"{variant}-x",
    }
    base.update(kw)
    return base


def test_variant_metrics_and_delta() -> None:
    rows = [
        _row("A", total_tokens=1000, cost_usd=0.01, latency_ms=4000.0, resolved=False, tool_error=True),
        _row("A", total_tokens=1000, cost_usd=0.01, latency_ms=4000.0, resolved=False, tool_error=True),
        _row("D", total_tokens=400, cost_usd=0.002, latency_ms=2000.0, resolved=True, tool_error=False),
        _row("D", total_tokens=400, cost_usd=0.002, latency_ms=2000.0, resolved=True, tool_error=False),
    ]
    table = arch_scoring.build_ablation_table(rows)
    metrics = {m["variant"]: m for m in table["variants"]}
    assert metrics["A"]["auto_resolve_rate"] == 0.0
    assert metrics["D"]["auto_resolve_rate"] == 1.0
    assert metrics["D"]["mean_total_tokens"] == 400
    delta = table["delta_d_vs_a"]
    assert delta["token_pct"] == (400 - 1000) / 1000 * 100  # -60%
    assert delta["auto_resolve_pp"] == 100.0


def test_build_summary_and_render_markdown() -> None:
    rows = [
        _row("A", resolved=False, score=0.2, tool_error=True),
        _row("D", resolved=True, score=0.9),
    ]
    summary = arch_scoring.build_summary(rows)
    md = arch_scoring.render_markdown(summary)
    assert "Architecture Ablation Table" in md
    assert "Failure-Mode Report" in md
    assert "Δ (D vs A)" in md


def test_errored_rows_excluded_from_quality_metrics() -> None:
    rows = [
        _row("D", outcome="error", resolved=False, score=0.0),
        _row("D", resolved=True, score=1.0),
    ]
    metrics = arch_scoring.variant_metrics(rows, "D")
    assert metrics["n"] == 1
    assert metrics["errored"] == 1
    assert metrics["auto_resolve_rate"] == 1.0


# --------------------------------------------------------------------------- #
# variants
# --------------------------------------------------------------------------- #


def test_variant_registry_axes() -> None:
    assert set(["A", "B", "C", "D", "D_triage_vertical"]).issubset(VARIANTS)
    assert VARIANTS["A"].topology == "single"
    assert VARIANTS["B"].handoff == "full_transcript"
    assert VARIANTS["C"].business_strategy == "react"
    assert VARIANTS["D"].business_strategy == "plan_execute"
    assert VARIANTS["D"].triage_tier == "triage"
    assert VARIANTS["D_triage_vertical"].triage_tier == "vertical"


def test_build_variant_smoke_all() -> None:
    toolbelt = ToolBelt([])
    for key in ["A", "B", "C", "D", "D_triage_vertical"]:
        runner = build_variant(
            VARIANTS[key], checkpointer=MemorySaver(), toolbelt=toolbelt
        )
        assert runner.spec.key == key
        assert runner.graph is not None
        assert callable(runner.run_fn)


def test_supervisor_default_options_is_variant_d() -> None:
    supervisor = SupervisorGraph(checkpointer=MemorySaver(), toolbelt=ToolBelt([]))
    assert supervisor.options == GraphOptions()
    assert supervisor.options.handoff == "structured"
    assert supervisor.options.business_strategy == "plan_execute"
    assert supervisor.options.triage_tier == "triage"


# --------------------------------------------------------------------------- #
# judge (no-LLM paths)
# --------------------------------------------------------------------------- #


async def test_judge_blocked_returns_unresolved() -> None:
    verdict = await ResolutionJudge().judge(
        prompt="p", rubric="r", final_answer="some answer", blocked=True
    )
    assert verdict.resolved is False
    assert verdict.score == 0.0


async def test_judge_empty_answer_returns_unresolved() -> None:
    verdict = await ResolutionJudge().judge(
        prompt="p", rubric="r", final_answer="   "
    )
    assert verdict.resolved is False


def test_resolution_verdict_schema_bounds() -> None:
    verdict = ResolutionVerdict(resolved=True, score=0.75, reason="ok")
    assert verdict.score == 0.75
