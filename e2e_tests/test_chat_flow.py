"""End-to-end chat: Triage → Billing → response, with MemorySaver-backed resume.

Mocks the LLM layer (Ollama) so the test can run anywhere; the rest of the
pipeline (LangGraph, MemorySaver, capability gate, output guardrails) is real.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver
from resolveai_api.agents.billing_graph import Plan, Replan, Response
from resolveai_api.agents.supervisor import SupervisorGraph
from resolveai_api.agents.triage import TriageOutput
from resolveai_api.config import get_settings


@pytest.fixture(autouse=True)
def _hermetic_guardrails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin guardrails off for this hermetic e2e regardless of ambient env.

    These tests mock the LLM layer but not the guardrails; if an outer process
    (e.g. a live-eval script) exported GUARDRAIL_L3=on, the real output guardrail
    would run on the canned mock reply and flip the final event from `done` to
    `blocked`. Setting them explicitly here makes the test self-contained.
    """
    for layer in ("GUARDRAIL_L1", "GUARDRAIL_L2", "GUARDRAIL_L3", "GUARDRAIL_L4"):
        monkeypatch.setenv(layer, "off")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class _Queue:
    """Replays a fixed sequence of values across `ainvoke` calls."""

    def __init__(self, values: list[Any]) -> None:
        self._values = list(values)

    async def ainvoke(self, _messages):
        if not self._values:
            raise AssertionError("LLM called more times than expected")
        return self._values.pop(0)

    def bind_tools(self, _tools):
        return self


def _structured_factory(plan_first: bool):
    """Return a `make_structured_llm` impl that maps schema → fixed result.

    `plan_first=True`: includes Plan + Replan answers (1 full billing turn).
    """
    if plan_first:
        triage_q = _Queue([TriageOutput(intent="billing", entities={}, confidence=0.9)])
        plan_q = _Queue([Plan(steps=["fetch charges", "issue refund"])])
        replan_q = _Queue(
            [
                Replan(
                    plan=None,
                    response=Response(
                        final_answer="Refund processed for ch_001.",
                        escalate=False,
                    ),
                )
            ]
        )
    else:
        triage_q = _Queue([TriageOutput(intent="billing", entities={}, confidence=0.9)])
        plan_q = _Queue([Plan(steps=["confirm refund landed"])])
        replan_q = _Queue(
            [
                Replan(
                    plan=None,
                    response=Response(
                        final_answer="Confirmed; refund was already issued.",
                        escalate=False,
                    ),
                )
            ]
        )

    def factory(_tier, schema):
        if schema is TriageOutput:
            return triage_q
        if schema is Plan:
            return plan_q
        if schema is Replan:
            return replan_q
        raise AssertionError(f"unexpected structured schema: {schema}")

    return factory


@pytest.mark.asyncio
async def test_chat_flow_completes_end_to_end() -> None:
    saver = MemorySaver()

    structured = _structured_factory(plan_first=True)
    executor_llm = _Queue([AIMessage(content="ok"), AIMessage(content="ok")])

    with (
        patch(
            "resolveai_api.agents.triage.make_structured_llm", side_effect=structured
        ),
        patch(
            "resolveai_api.agents.billing_graph.make_structured_llm",
            side_effect=structured,
        ),
        patch(
            "resolveai_api.agents.billing_graph.make_llm", return_value=executor_llm
        ),
    ):
        supervisor = SupervisorGraph(checkpointer=saver, mcp_tools=[])
        events: list[dict[str, str]] = []
        async for evt in supervisor.stream(
            message="I was double charged for $99 last month.",
            customer_id="cus_demo_001",
            tenant_id="demo",
            thread_id="t-e2e",
        ):
            events.append(evt)

    event_types = [e["type"] for e in events]
    assert event_types[-1] == "done"
    assert any(e["type"] == "agent_step" for e in events)


@pytest.mark.asyncio
async def test_thread_state_resumes_from_checkpointer() -> None:
    saver = MemorySaver()
    namespace = "demo::cus_demo_001::t-resume"

    # Round 1
    structured1 = _structured_factory(plan_first=True)
    executor_llm = _Queue([AIMessage(content="ok"), AIMessage(content="ok")])
    with (
        patch(
            "resolveai_api.agents.triage.make_structured_llm", side_effect=structured1
        ),
        patch(
            "resolveai_api.agents.billing_graph.make_structured_llm",
            side_effect=structured1,
        ),
        patch(
            "resolveai_api.agents.billing_graph.make_llm", return_value=executor_llm
        ),
    ):
        supervisor = SupervisorGraph(checkpointer=saver, mcp_tools=[])
        async for _ in supervisor.stream(
            message="I was double charged.",
            customer_id="cus_demo_001",
            tenant_id="demo",
            thread_id="t-resume",
        ):
            pass

    # Inspect state via checkpointer (proves the checkpoint exists for this thread)
    config = {"configurable": {"thread_id": namespace}}
    state = await supervisor.graph.aget_state(config)
    assert state is not None
    # The stored state must include messages from round 1.
    stored_messages = state.values.get("messages", []) if state.values else []
    assert len(stored_messages) >= 2  # human prompt + at least one AIMessage

    # Round 2 — same thread_id, new prompt: history is reused.
    structured2 = _structured_factory(plan_first=False)
    executor_llm2 = _Queue([AIMessage(content="ok")])
    with (
        patch(
            "resolveai_api.agents.triage.make_structured_llm", side_effect=structured2
        ),
        patch(
            "resolveai_api.agents.billing_graph.make_structured_llm",
            side_effect=structured2,
        ),
        patch(
            "resolveai_api.agents.billing_graph.make_llm", return_value=executor_llm2
        ),
    ):
        supervisor2 = SupervisorGraph(checkpointer=saver, mcp_tools=[])
        async for _ in supervisor2.stream(
            message="Did the refund go through?",
            customer_id="cus_demo_001",
            tenant_id="demo",
            thread_id="t-resume",
        ):
            pass

    final_state = await supervisor2.graph.aget_state(config)
    final_messages = final_state.values.get("messages", []) if final_state.values else []
    # Round 2 appended at least one human + one AI message on top of round 1.
    assert len(final_messages) > len(stored_messages)
