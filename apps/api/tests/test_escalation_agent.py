"""Escalation Agent — deterministic Slack + Zendesk handoff."""

from __future__ import annotations

from typing import Any, ClassVar

import pytest
from langchain_core.tools import BaseTool
from resolveai_api.agents.escalation import EscalationAgent
from resolveai_api.agents.state import GraphState
from resolveai_api.core.executor import Executor


class _FakeSlackNotify(BaseTool):
    name: str = "slack_notify_team"
    description: str = "fake slack"
    metadata: ClassVar[dict[str, Any]] = {
        "server": "slack",
        "capability": "write",
        "full_name": "slack.notify_team",
    }

    def _run(self, *args, **kwargs):
        return {"channel": kwargs.get("channel", "#oncall-billing")}

    async def _arun(self, *args, **kwargs):
        return {"channel": kwargs.get("channel"), "text": kwargs.get("message")}


class _FakeZendeskEscalate(BaseTool):
    name: str = "zendesk_escalate"
    description: str = "fake zendesk escalate"
    metadata: ClassVar[dict[str, Any]] = {
        "server": "zendesk",
        "capability": "destructive",
        "full_name": "zendesk.escalate",
    }

    def _run(self, *args, **kwargs):
        return {"id": kwargs.get("ticket_id"), "status": "escalated"}

    async def _arun(self, *args, **kwargs):
        return {"id": kwargs.get("ticket_id"), "status": "escalated"}


@pytest.mark.asyncio
async def test_escalation_calls_slack_and_zendesk_when_granted() -> None:
    agent = EscalationAgent.default(
        tools=[_FakeSlackNotify(), _FakeZendeskEscalate()],
        executor=Executor(),
    )
    state: GraphState = {
        "messages": [],
        "tenant_id": "demo",
        "customer_id": "cus_demo_001",
        "thread_id": "t-1",
        "tool_calls": [],
        "guardrail_flags": [],
        "ticket_summary": {
            "intent": "billing",
            "entities": {"ticket_id": "zd_001"},
        },
    }

    result = await agent.run(state)
    steps = [tc["step"] for tc in result["tool_calls"]]
    assert "slack.notify_team" in steps
    assert "zendesk.escalate" in steps
    assert "I've escalated this" in result["messages"][0].content


@pytest.mark.asyncio
async def test_escalation_routes_to_technical_channel_for_technical_intent() -> None:
    agent = EscalationAgent.default(
        tools=[_FakeSlackNotify()], executor=Executor()
    )
    state: GraphState = {
        "messages": [],
        "customer_id": "cus_demo_001",
        "tool_calls": [],
        "ticket_summary": {"intent": "technical", "entities": {}},
    }

    result = await agent.run(state)
    notify = next(tc for tc in result["tool_calls"] if tc["step"] == "slack.notify_team")
    observation = notify["observation"]
    assert "#oncall-technical" in observation


@pytest.mark.asyncio
async def test_escalation_without_tools_still_returns_message() -> None:
    agent = EscalationAgent.default(tools=[], executor=Executor())
    state: GraphState = {
        "messages": [],
        "customer_id": "cus_demo_001",
        "tool_calls": [],
        "ticket_summary": {"intent": "billing", "entities": {}},
    }

    result = await agent.run(state)
    assert "manual escalation required" in result["messages"][0].content.lower() or (
        "logged escalation locally" in result["messages"][0].content.lower()
    )
