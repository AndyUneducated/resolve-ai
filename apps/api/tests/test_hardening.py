"""Hardening fixes (LM-free): other-intent fallback, real escalation routing,
fail-closed guardrails, per-request cost metrics, and readiness probe.

All LLM calls are mocked/faked — nothing here touches Ollama.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import Response
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver
from resolveai_api.agents.billing_graph import Plan, Replan
from resolveai_api.agents.billing_graph import Response as BillingResponse
from resolveai_api.agents.supervisor import SupervisorGraph
from resolveai_api.agents.triage import OTHER_INTENT_FALLBACK, TriageOutput
from resolveai_api.api.health import readyz
from resolveai_api.config import get_settings
from resolveai_api.guardrails.attribution import (
    BlockKind,
    block_kind,
    has_degraded_flag,
    resolve_fail_closed,
)


@pytest.fixture(autouse=True)
def _guardrails_off(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for layer in ("GUARDRAIL_L1", "GUARDRAIL_L2", "GUARDRAIL_L3", "GUARDRAIL_L4"):
        monkeypatch.setenv(layer, "off")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class _Queue:
    def __init__(self, values: list[Any]) -> None:
        self._values = list(values)

    async def ainvoke(self, _messages: Any) -> Any:
        if not self._values:
            raise AssertionError("LLM called more times than expected")
        return self._values.pop(0)

    def bind_tools(self, _tools: Any) -> _Queue:
        return self


async def _collect(supervisor: SupervisorGraph, **kwargs: Any) -> list[dict[str, str]]:
    return [evt async for evt in supervisor.stream(**kwargs)]


@pytest.mark.asyncio
async def test_other_intent_emits_graceful_fallback() -> None:
    """`other` intent must yield a helpful reply, not an empty stream or an echo."""
    triage = _Queue([TriageOutput(intent="other", entities={}, confidence=0.3)])

    def factory(_tier: Any, schema: Any) -> Any:
        assert schema is TriageOutput
        return triage

    with patch("resolveai_api.agents.triage.make_structured_llm", side_effect=factory):
        supervisor = SupervisorGraph(checkpointer=MemorySaver(), mcp_tools=[])
        events = await _collect(
            supervisor,
            message="hey, random unrelated musings",
            customer_id="c1",
            tenant_id="demo",
            thread_id="t-other",
        )

    steps = [json.loads(e["data"]) for e in events if e["type"] == "agent_step"]
    assert len(steps) == 1  # exactly one reply, no user-input echo
    assert steps[0]["agent"] == "triage"
    assert steps[0]["content"] == OTHER_INTENT_FALLBACK
    assert events[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_done_event_carries_cost_metrics() -> None:
    triage = _Queue([TriageOutput(intent="other", entities={}, confidence=0.3)])
    with patch(
        "resolveai_api.agents.triage.make_structured_llm", return_value=triage
    ):
        supervisor = SupervisorGraph(checkpointer=MemorySaver(), mcp_tools=[])
        events = await _collect(
            supervisor,
            message="anything",
            customer_id="c1",
            tenant_id="demo",
            thread_id="t-cost",
        )

    done = json.loads(events[-1]["data"])
    for key in (
        "tokens",
        "input_tokens",
        "output_tokens",
        "cost_usd",
        "tool_calls",
        "guardrail_latency_ms",
        "fail_closed",
    ):
        assert key in done
    assert set(done["guardrail_latency_ms"]) == {"input", "output"}


def test_has_degraded_flag() -> None:
    assert has_degraded_flag(["llama_guard_timeout"])
    assert has_degraded_flag(["policy_judge_unavailable", "pii:email"])
    assert not has_degraded_flag(["pii:email", "indirect_injection_suspected"])


def test_resolve_fail_closed_explicit_and_profile() -> None:
    # Explicit overrides win regardless of profile.
    assert resolve_fail_closed("on", "demo") is True
    assert resolve_fail_closed("off", "production") is False
    # "auto"/unset follows the profile.
    assert resolve_fail_closed("auto", "production") is True
    assert resolve_fail_closed("auto", "demo") is False
    assert resolve_fail_closed("anything-else", "demo") is False


def test_block_kind_classifies_true_positive_vs_degraded() -> None:
    assert block_kind(["blocked"], fail_closed=True) is BlockKind.TRUE_POSITIVE
    assert block_kind(["policy:x"], fail_closed=False) is BlockKind.TRUE_POSITIVE
    assert block_kind(["llama_guard_timeout"], fail_closed=True) is BlockKind.DEGRADED
    # Same degraded flag is NOT a block when fail-open.
    assert block_kind(["llama_guard_timeout"], fail_closed=False) is BlockKind.NONE
    assert block_kind(["pii:email"], fail_closed=True) is BlockKind.NONE


@pytest.mark.asyncio
async def test_production_profile_defaults_to_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ENV_PROFILE=production + fail_closed=auto → a degraded guard blocks."""
    monkeypatch.setenv("ENV_PROFILE", "production")
    monkeypatch.delenv("GUARDRAIL_FAIL_CLOSED", raising=False)
    get_settings.cache_clear()

    supervisor = SupervisorGraph(checkpointer=MemorySaver(), mcp_tools=[])

    async def _degraded(_text: str) -> tuple[str, list[str]]:
        return _text, ["policy_judge_unavailable"]

    monkeypatch.setattr(supervisor.input_guard, "scan_and_redact", _degraded)

    events = await _collect(
        supervisor,
        message="please help",
        customer_id="c1",
        tenant_id="demo",
        thread_id="t-prod",
    )
    assert events[0]["type"] == "blocked"
    payload = json.loads(events[0]["data"])
    assert payload["kind"] == BlockKind.DEGRADED


@pytest.mark.asyncio
async def test_fail_closed_blocks_on_degraded_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GUARDRAIL_FAIL_CLOSED", "on")
    get_settings.cache_clear()

    supervisor = SupervisorGraph(checkpointer=MemorySaver(), mcp_tools=[])

    async def _degraded(_text: str) -> tuple[str, list[str]]:
        return _text, ["llama_guard_timeout"]

    monkeypatch.setattr(supervisor.input_guard, "scan_and_redact", _degraded)

    events = await _collect(
        supervisor,
        message="please help",
        customer_id="c1",
        tenant_id="demo",
        thread_id="t-fc",
    )
    assert events[0]["type"] == "blocked"


@pytest.mark.asyncio
async def test_billing_escalation_is_a_real_graph_handoff() -> None:
    """When Billing requests escalation, the escalation node actually runs."""
    triage = _Queue([TriageOutput(intent="billing", entities={}, confidence=0.9)])
    plan = _Queue([Plan(steps=["review high-value charge"])])
    replan = _Queue(
        [
            Replan(
                plan=None,
                response=BillingResponse(
                    final_answer="This $1200 charge needs manager review.",
                    escalate=True,
                ),
            )
        ]
    )

    def factory(_tier: Any, schema: Any) -> Any:
        if schema is TriageOutput:
            return triage
        if schema is Plan:
            return plan
        if schema is Replan:
            return replan
        raise AssertionError(f"unexpected schema {schema}")

    executor_llm = _Queue([AIMessage(content="ok")] * 4)

    with (
        patch("resolveai_api.agents.triage.make_structured_llm", side_effect=factory),
        patch(
            "resolveai_api.agents.billing_graph.make_structured_llm",
            side_effect=factory,
        ),
        patch(
            "resolveai_api.agents.billing_graph.make_llm", return_value=executor_llm
        ),
    ):
        supervisor = SupervisorGraph(checkpointer=MemorySaver(), mcp_tools=[])
        events = await _collect(
            supervisor,
            message="refund my $1200 charge",
            customer_id="cus_demo_001",
            tenant_id="demo",
            thread_id="t-esc",
        )

    agents = [
        json.loads(e["data"])["agent"] for e in events if e["type"] == "agent_step"
    ]
    assert "billing" in agents
    assert "escalation" in agents  # real handoff, not just an advisory text suffix


@pytest.mark.asyncio
async def test_readyz_reports_structured_checks_and_degrades() -> None:
    """readyz never crashes; with no DB/MCP it degrades to 503 with per-check status."""
    request: Any = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(toolbelt=None, mcp_tools=[]))
    )
    response = Response()
    result = await readyz(request, response)

    checks = result["checks"]
    assert isinstance(checks, dict)
    assert set(checks) == {"db", "mcp"}
    assert result["status"] in ("ok", "degraded")
    # No Postgres in unit tests → db down, mcp has no tools → degraded + 503.
    assert result["status"] == "degraded"
    assert response.status_code == 503
