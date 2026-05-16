"""端到端：用户发一句话 → Triage → Billing → 流式返回。

当前是 stub graph，只验证 wiring 正确（不依赖真 LLM / 真 MCP server）。
"""

from __future__ import annotations

import pytest
from resolveai_api.agents.supervisor import SupervisorGraph


@pytest.mark.asyncio
async def test_supervisor_streams_steps() -> None:
    sup = SupervisorGraph()
    events: list[dict[str, str]] = []
    async for evt in sup.stream(
        message="我上个月被多扣了 $99",
        customer_id="cust-001",
        tenant_id="demo",
        thread_id="t-1",
    ):
        events.append(evt)

    types = [e["type"] for e in events]
    assert "done" in types
