"""M11 — observability (/metrics) + cost governance (budget circuit-breaker).

All LM-free: the pure budget math and routers use a hand-built `RunTrace`; the
end-to-end breaker test uses `LLM_BACKEND=fake` (deterministic token accounting),
never touching Ollama.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver
from resolveai_api.agents.billing_graph import (
    BillingState,
    _route_after_executor,
    _route_after_replanner,
)
from resolveai_api.agents.supervisor import SupervisorGraph
from resolveai_api.config import get_settings
from resolveai_api.core.budget import is_over_budget, over_cost_budget
from resolveai_api.core.usage import RunTrace, capture_run
from resolveai_api.main import create_app
from resolveai_api.observability import metrics


@pytest.fixture(autouse=True)
def _fast_deterministic_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for layer in ("GUARDRAIL_L1", "GUARDRAIL_L2", "GUARDRAIL_L3", "GUARDRAIL_L4"):
        monkeypatch.setenv(layer, "off")
    monkeypatch.setenv("LLM_BACKEND", "fake")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _trace(input_tokens: int, output_tokens: int) -> RunTrace:
    trace = RunTrace()
    trace.add_usage("vertical", input_tokens=input_tokens, output_tokens=output_tokens)
    return trace


# --------------------------- pure budget math ---------------------------


def test_is_over_budget_is_safe_and_correct() -> None:
    assert is_over_budget(None, 0.05) is False  # no active run
    assert is_over_budget(_trace(1_000_000, 1_000_000), 0.0) is False  # <=0 disables
    assert is_over_budget(_trace(1, 1), 0.05) is False  # negligible cost
    # vertical @ (3.00 in / 15.00 out per Mtok): 0.3 + 1.5 = $1.8 > $0.05
    assert is_over_budget(_trace(100_000, 100_000), 0.05) is True


# --------------------------- router circuit-breaker ---------------------------


def test_routers_force_done_when_over_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COST_BUDGET_USD", "0.0001")
    get_settings.cache_clear()
    state: BillingState = {"plan": ["a step"], "past_steps": [], "iter_count": 1}
    with capture_run() as trace:
        trace.add_usage("vertical", input_tokens=1_000, output_tokens=1_000)  # ~$0.018
        assert over_cost_budget() is True
        assert _route_after_executor(state) == "done"
        assert _route_after_replanner(state) == "done"


def test_routers_unchanged_under_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COST_BUDGET_USD", "1000")  # effectively unlimited
    get_settings.cache_clear()
    state: BillingState = {"plan": ["a step"], "past_steps": [], "iter_count": 1}
    with capture_run() as trace:
        trace.add_usage("vertical", input_tokens=10, output_tokens=10)
        assert over_cost_budget() is False
        assert _route_after_executor(state) == "execute"  # plan remains → keep going


# --------------------------- metric recorders ---------------------------


def test_metric_recorders_increment() -> None:
    if not metrics.available():
        pytest.skip("prometheus_client not installed")
    done_before = metrics.TICKETS.labels(outcome="done")._value.get()
    budget_before = metrics.BUDGET_EXCEEDED._value.get()
    metrics.record_done(
        cost_usd=0.01,
        tokens=120,
        tool_calls=2,
        tool_errors=1,
        guardrail_latency_ms={"input": 1.5, "output": 2.0},
        over_budget=True,
    )
    assert metrics.TICKETS.labels(outcome="done")._value.get() == done_before + 1
    assert metrics.BUDGET_EXCEEDED._value.get() == budget_before + 1

    block_before = metrics.GUARDRAIL_BLOCKS.labels(
        layer="input", kind="true_positive"
    )._value.get()
    metrics.record_block("input", "true_positive")
    assert (
        metrics.GUARDRAIL_BLOCKS.labels(layer="input", kind="true_positive")._value.get()
        == block_before + 1
    )


# --------------------------- /metrics endpoint ---------------------------


def test_metrics_endpoint_exposes_prometheus_families() -> None:
    # Plain TestClient (no `with`) so the Postgres-connecting lifespan never runs;
    # /metrics needs no app.state.
    client = TestClient(create_app())
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    for family in (
        "resolveai_tickets_total",
        "resolveai_guardrail_blocks_total",
        "resolveai_ticket_cost_usd",
        "resolveai_cost_budget_exceeded_total",
        "resolveai_guardrail_latency_ms",
    ):
        assert family in response.text


# --------------------------- end-to-end breaker ---------------------------


@pytest.mark.asyncio
async def test_cost_budget_breaker_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    """A tiny budget makes a real (fake-backend) billing run trip the breaker."""
    monkeypatch.setenv("COST_BUDGET_USD", "0.0001")
    get_settings.cache_clear()

    supervisor = SupervisorGraph(checkpointer=MemorySaver(), mcp_tools=[])
    events = [
        evt
        async for evt in supervisor.stream(
            message="please refund my charge",
            customer_id="c1",
            tenant_id="demo",
            thread_id="t-budget",
        )
    ]

    assert events[-1]["type"] == "done"
    done = json.loads(events[-1]["data"])
    assert done["over_budget"] is True
    assert done["cost_budget_usd"] == pytest.approx(0.0001)
    assert done["cost_usd"] > 0.0001  # real modeled cost exceeded the budget
