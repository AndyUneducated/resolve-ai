"""Milestone 8 unit tests: fake backend, chaos aggregation, EvalGate payload,
regression gate, and the OTel span helper no-op path."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage, SystemMessage
from resolveai_api.config import get_settings

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _use_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_BACKEND", "fake")
    get_settings.cache_clear()


# --------------------------------------------------------------------------- #
# Fake backend
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_fake_chat_model_returns_canned_with_usage(monkeypatch):
    _use_fake(monkeypatch)
    from resolveai_api.core._fake_llm import FakeChatModel
    from resolveai_api.core.llm import make_llm

    llm = make_llm("vertical")
    assert isinstance(llm, FakeChatModel)
    msg = await llm.ainvoke([HumanMessage(content="hi")])
    assert "refund" in msg.content.lower()
    assert msg.usage_metadata["total_tokens"] == 72


@pytest.mark.asyncio
async def test_fake_usage_captured_under_capture_run(monkeypatch):
    _use_fake(monkeypatch)
    from resolveai_api.core.llm import make_llm
    from resolveai_api.core.usage import capture_run

    with capture_run() as trace:
        await make_llm("vertical").ainvoke([HumanMessage(content="hi")])
    assert trace.total_tokens == 72
    assert "vertical" in trace.usage_by_tier


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("I was double charged, please refund ch_001", "billing"),
        ("I get a login error, how do i fix the api", "technical"),
        ("escalate this fraud charge to a manager", "escalation"),
    ],
)
async def test_fake_structured_triage_routes_by_keyword(monkeypatch, text, expected):
    _use_fake(monkeypatch)
    from resolveai_api.agents.triage import TriageOutput
    from resolveai_api.core.llm import make_structured_llm

    runnable = make_structured_llm("triage", TriageOutput)
    out = await runnable.ainvoke(
        [SystemMessage(content="classify into billing|technical|escalation"),
         HumanMessage(content=text)]
    )
    assert isinstance(out, TriageOutput)
    assert out.intent == expected


@pytest.mark.asyncio
async def test_fake_structured_plan_and_replan(monkeypatch):
    _use_fake(monkeypatch)
    from resolveai_api.agents.billing_graph import Plan, Replan
    from resolveai_api.core.llm import make_structured_llm

    plan = await make_structured_llm("vertical", Plan).ainvoke("plan it")
    assert isinstance(plan, Plan) and plan.steps

    replan = await make_structured_llm("vertical", Replan).ainvoke("finalize")
    assert isinstance(replan, Replan)
    assert replan.response is not None and replan.response.final_answer


# --------------------------------------------------------------------------- #
# EvalGate payload + client
# --------------------------------------------------------------------------- #


def test_build_run_summary_from_trace():
    from resolveai_api.core.usage import RunTrace, ToolCallRecord
    from resolveai_api.observability.evalgate import build_run_summary

    trace = RunTrace()
    trace.add_usage("triage", input_tokens=100, output_tokens=20)
    trace.add_usage("vertical", input_tokens=400, output_tokens=200)
    trace.add_tool_call(
        ToolCallRecord(
            tool="stripe.refund", args={}, output_text="ok", is_error=False, duration_ms=5.0
        )
    )
    trace.add_tool_call(
        ToolCallRecord(
            tool="stripe.list", args={}, output_text="error: x", is_error=True, duration_ms=5.0
        )
    )

    summary = build_run_summary(
        trace, ticket_id="t1", latency_ms=1234.5, resolved=True, score=0.9
    )
    assert summary["total_tokens"] == 720
    assert summary["tool_calls"] == 2
    assert summary["tool_error_count"] == 1
    assert summary["tool_error"] is True
    assert summary["cost_usd"] > 0
    assert summary["resolved"] is True
    assert set(summary["usage_by_tier"]) == {"triage", "vertical"}


@pytest.mark.asyncio
async def test_evalgate_client_noop_without_endpoint():
    from resolveai_api.observability.evalgate import EvalGateClient

    client = EvalGateClient(endpoint="")
    assert client.enabled is False
    delivered = await client.push(ticket_id="t1", payload={"x": 1})
    assert delivered is False


# --------------------------------------------------------------------------- #
# Chaos aggregation (scripts/chaos_load.py)
# --------------------------------------------------------------------------- #


def test_chaos_summarize_percentiles_and_gate():
    import chaos_load

    rows = [
        {"id": f"c{i}", "category": "billing", "outcome": "ok", "blocked": False,
         "latency_ms": float(i * 100), "error": None}
        for i in range(1, 11)
    ]
    rows.append({"id": "err", "category": "billing", "outcome": "error",
                 "blocked": False, "latency_ms": 0.0, "error": "boom"})
    args = argparse.Namespace(
        backend="fake", guardrails=False, with_tools=False, concurrency=10, p95_target=6.0
    )
    summary = chaos_load._summarize(rows, wall_s=2.0, args=args)
    assert summary["total"] == 11
    assert summary["completed"] == 10
    assert summary["errors"] == 1
    assert summary["throughput_rps"] == pytest.approx(5.5)
    assert summary["latency_ms"]["p50"] == pytest.approx(550.0, abs=1.0)
    assert summary["p95_pass"] is True  # 1000ms p95 << 6000ms target


def test_chaos_ticket_templates_cover_all_intents():
    import chaos_load

    cats = {chaos_load._make_ticket(i)["category"] for i in range(len(chaos_load._TEMPLATES))}
    assert {"billing", "technical", "escalation"} <= cats


# --------------------------------------------------------------------------- #
# Regression gate (scripts/regression_gate.py)
# --------------------------------------------------------------------------- #


def _baseline() -> dict:
    return {
        "metrics": {
            "p95_latency_ms": 1000.0,
            "mean_cost_usd": 0.001,
            "auto_resolve_rate": 0.9,
            "tool_error_rate": 0.05,
        },
        "thresholds": {
            "p95_latency_ms_max_increase_pct": 50.0,
            "mean_cost_usd_max_increase_pct": 50.0,
            "auto_resolve_rate_min_drop_pp": 5.0,
            "tool_error_rate_max_increase_pp": 5.0,
        },
    }


def test_regression_gate_passes_within_thresholds():
    import regression_gate

    current = {
        "p95_latency_ms": 1100.0,
        "mean_cost_usd": 0.0011,
        "auto_resolve_rate": 0.88,
        "tool_error_rate": 0.06,
    }
    assert regression_gate._check_regressions(current, _baseline()) == []


def test_regression_gate_flags_latency_and_resolve_drop():
    import regression_gate

    current = {
        "p95_latency_ms": 2000.0,        # +100% > 50%
        "mean_cost_usd": 0.001,
        "auto_resolve_rate": 0.80,       # -10pp > 5pp
        "tool_error_rate": 0.05,
    }
    violations = regression_gate._check_regressions(current, _baseline())
    assert len(violations) == 2
    assert any("p95_latency_ms" in v for v in violations)
    assert any("auto_resolve_rate" in v for v in violations)


# --------------------------------------------------------------------------- #
# OTel span helper (no-op when tracer is None)
# --------------------------------------------------------------------------- #


def test_span_helper_noop_without_tracer():
    from resolveai_api.observability.tracing import span

    with span(None, "x", attributes={"a": 1}) as s:
        assert s is None
