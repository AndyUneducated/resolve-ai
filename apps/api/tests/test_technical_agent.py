"""Technical Agent — pulls Zendesk ticket history as M3 context (M6 adds KB)."""

from __future__ import annotations

from typing import Any, ClassVar

import pytest
from langchain_core.tools import BaseTool
from resolveai_api.agents.state import GraphState
from resolveai_api.agents.technical import TechnicalAgent
from resolveai_api.core.executor import Executor


class _FakeHistory(BaseTool):
    name: str = "zendesk_get_ticket_history"
    description: str = "fake history"
    metadata: ClassVar[dict[str, Any]] = {
        "server": "zendesk",
        "capability": "read",
        "full_name": "zendesk.get_ticket_history",
    }

    def _run(self, *args, **kwargs):
        return []

    async def _arun(self, *args, **kwargs):
        return [
            {"id": "zd_010", "subject": "API 502 spikes", "status": "open"},
            {"id": "zd_011", "subject": "Webhook retries", "status": "solved"},
        ]


@pytest.mark.asyncio
async def test_technical_fetches_ticket_history_when_tool_present() -> None:
    agent = TechnicalAgent.default(tools=[_FakeHistory()], executor=Executor())
    state: GraphState = {
        "messages": [],
        "customer_id": "cus_demo_001",
        "tool_calls": [],
    }
    result = await agent.run(state)
    steps = [tc["step"] for tc in result["tool_calls"]]
    assert "zendesk.get_ticket_history" in steps
    content = result["messages"][0].content
    assert "API 502 spikes" in content


@pytest.mark.asyncio
async def test_technical_falls_back_when_no_tools() -> None:
    agent = TechnicalAgent.default(tools=[], executor=Executor())
    state: GraphState = {
        "messages": [],
        "customer_id": "cus_demo_001",
        "tool_calls": [],
    }
    result = await agent.run(state)
    assert result["tool_calls"] == []
    assert "M6" in result["messages"][0].content
