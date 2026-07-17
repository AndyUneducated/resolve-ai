"""Billing Plan-Execute-Replan sub-graph — covers the three-node closed loop."""

from __future__ import annotations

from typing import Any, ClassVar
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool
from resolveai_api.agents.billing_graph import (
    MAX_STEPS,
    Plan,
    Replan,
    Response,
    build_billing_subgraph,
)


class _StubTool(BaseTool):
    name: str = "stripe_list_charges"
    description: str = "stub"
    metadata: ClassVar[dict[str, Any]] = {
        "server": "stripe",
        "capability": "read",
        "full_name": "stripe.list_charges",
    }

    def _run(self, *args, **kwargs):  # pragma: no cover — async path used
        return "stub"

    async def _arun(self, *args, **kwargs):
        return [{"id": "ch_001", "amount": 9900}]


class _RecordingExecutorLLM:
    """Fake LLM bound to tools; emits a tool_call once, then plain text."""

    def __init__(self) -> None:
        self.invocations = 0

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        self.invocations += 1
        if self.invocations == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "stripe_list_charges",
                        "args": {"customer_id": "cus_demo_001"},
                        "id": "call_1",
                        "type": "tool_call",
                    }
                ],
            )
        return AIMessage(content="step satisfied by past observations")


class _FixedStructuredLLM:
    def __init__(self, returns: list[Any]) -> None:
        self._queue = list(returns)
        self.calls = 0

    async def ainvoke(self, messages):
        self.calls += 1
        return self._queue.pop(0)


@pytest.mark.asyncio
async def test_plan_execute_replan_completes() -> None:
    plan = Plan(steps=["fetch charges", "verify with customer"])
    finalize = Replan(
        plan=None,
        response=Response(final_answer="Refund issued for ch_001.", escalate=False),
    )
    structured_lookup = {Plan: plan, Replan: finalize}

    def fake_make_structured_llm(_tier, schema):
        return _FixedStructuredLLM([structured_lookup[schema]])

    executor_llm = _RecordingExecutorLLM()

    with (
        patch(
            "resolveai_api.agents.billing_graph.make_structured_llm",
            side_effect=fake_make_structured_llm,
        ),
        patch(
            "resolveai_api.agents.billing_graph.make_llm",
            return_value=executor_llm,
        ),
    ):
        graph = build_billing_subgraph(
            tools=[_StubTool()], whitelist=["stripe.list_charges"]
        )
        result = await graph.ainvoke(
            {
                "messages": [],
                "ticket_summary": {"intent": "billing", "entities": {}},
                "plan": [],
                "past_steps": [],
                "iter_count": 0,
            }
        )

    assert result["response"] is not None
    assert "Refund issued" in result["response"].final_answer
    assert len(result["past_steps"]) >= 1
    # plan was consumed
    assert result.get("plan") in (None, [])


class _RaisingTool(BaseTool):
    """Granted (whitelisted) tool whose invocation raises a non-permission error."""

    name: str = "stripe_refund"
    description: str = "stub that raises upstream"
    metadata: ClassVar[dict[str, Any]] = {
        "server": "stripe",
        "capability": "write",
        "full_name": "stripe.refund",
    }

    def _run(self, *args, **kwargs):  # pragma: no cover — async path used
        raise RuntimeError("upstream 500")

    async def _arun(self, *args, **kwargs):
        raise RuntimeError("upstream 500")


class _CallRaisingToolLLM:
    """Executor LLM that always tries to call the raising tool."""

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "stripe_refund",
                    "args": {"charge_id": "ch_001", "amount": 100},
                    "id": "call_1",
                    "type": "tool_call",
                }
            ],
        )


@pytest.mark.asyncio
async def test_plan_execute_records_tool_error_instead_of_crashing() -> None:
    """A tool that raises should be recorded as an observation, not crash the graph.

    Regression test: the plan-execute executor node previously caught only
    PermissionError, so a real tool/network error propagated and killed the
    whole sub-graph (the ReAct loop already handled this).
    """
    plan = Plan(steps=["issue the refund"])
    finalize = Replan(
        plan=None,
        response=Response(final_answer="Escalating after tool failure.", escalate=True),
    )

    def fake_make_structured_llm(_tier, schema):
        return _FixedStructuredLLM([{Plan: plan, Replan: finalize}[schema]])

    with (
        patch(
            "resolveai_api.agents.billing_graph.make_structured_llm",
            side_effect=fake_make_structured_llm,
        ),
        patch(
            "resolveai_api.agents.billing_graph.make_llm",
            return_value=_CallRaisingToolLLM(),
        ),
    ):
        graph = build_billing_subgraph(
            tools=[_RaisingTool()], whitelist=["stripe.refund"]
        )
        result = await graph.ainvoke(
            {
                "messages": [],
                "ticket_summary": {"intent": "billing", "entities": {}},
                "plan": [],
                "past_steps": [],
                "iter_count": 0,
            }
        )

    # Graph completed (no exception) and the failure was captured as an observation.
    assert result["response"] is not None
    observations = " ".join(o for _, o in result["past_steps"])
    assert "error" in observations.lower()
    assert "upstream 500" in observations


@pytest.mark.asyncio
async def test_max_steps_truncates_runaway_loop() -> None:
    """Replanner that always returns 'continue' should not loop forever."""

    plan = Plan(steps=[f"step-{i}" for i in range(MAX_STEPS + 5)])
    keep_going = Replan(plan=Plan(steps=["loop"]), response=None)

    def fake_make_structured_llm(_tier, schema):
        if schema is Plan:
            return _FixedStructuredLLM([plan])
        return _FixedStructuredLLM([keep_going] * 100)

    class _Echo:
        def bind_tools(self, tools):
            return self

        async def ainvoke(self, messages):
            return AIMessage(content="ok")

    with (
        patch(
            "resolveai_api.agents.billing_graph.make_structured_llm",
            side_effect=fake_make_structured_llm,
        ),
        patch("resolveai_api.agents.billing_graph.make_llm", return_value=_Echo()),
    ):
        graph = build_billing_subgraph(tools=[], whitelist=[])
        result = await graph.ainvoke(
            {
                "messages": [],
                "ticket_summary": {},
                "plan": [],
                "past_steps": [],
                "iter_count": 0,
            }
        )

    assert (result.get("iter_count") or 0) >= MAX_STEPS
    assert len(result["past_steps"]) <= MAX_STEPS
