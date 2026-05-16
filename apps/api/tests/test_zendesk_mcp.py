"""Zendesk MCP server — list_tools, history, update, escalate, error paths."""

from __future__ import annotations

import json

import pytest
from mcp_servers.zendesk import data
from mcp_servers.zendesk.server import _call_tool, _list_tools


@pytest.mark.asyncio
async def test_list_tools_publishes_three_tools() -> None:
    tools = await _list_tools()
    names = {t.name for t in tools}
    assert names == {"get_ticket_history", "update_ticket", "escalate"}
    escalate = next(t for t in tools if t.name == "escalate")
    assert {"ticket_id", "reason"} <= set(escalate.inputSchema["properties"])


@pytest.mark.asyncio
async def test_get_ticket_history_returns_seeded_data() -> None:
    out = await _call_tool("get_ticket_history", {"customer_id": "cus_demo_001"})
    payload = json.loads(out[0].text)
    ids = {t["id"] for t in payload}
    assert {"zd_001", "zd_002"} <= ids
    assert all(t["customer_id"] == "cus_demo_001" for t in payload)


@pytest.mark.asyncio
async def test_update_ticket_appends_note_and_persists_status() -> None:
    out = await _call_tool(
        "update_ticket",
        {"ticket_id": "zd_001", "status": "pending", "note": "billing review"},
    )
    payload = json.loads(out[0].text)
    assert payload["status"] == "pending"
    assert "billing review" in payload["notes"]
    assert data.STORE.tickets["zd_001"].status == "pending"


@pytest.mark.asyncio
async def test_update_ticket_rejects_invalid_status() -> None:
    with pytest.raises(ValueError, match="invalid_status"):
        await _call_tool(
            "update_ticket",
            {"ticket_id": "zd_001", "status": "exploded"},
        )


@pytest.mark.asyncio
async def test_escalate_marks_ticket_and_records_reason() -> None:
    out = await _call_tool(
        "escalate",
        {"ticket_id": "zd_001", "reason": "duplicate charge, customer angry"},
    )
    payload = json.loads(out[0].text)
    assert payload["status"] == "escalated"
    assert any("duplicate charge" in n for n in payload["notes"])


@pytest.mark.asyncio
async def test_escalate_already_escalated_raises() -> None:
    with pytest.raises(ValueError, match="already_escalated"):
        await _call_tool(
            "escalate", {"ticket_id": "zd_004", "reason": "again please"}
        )


@pytest.mark.asyncio
async def test_unknown_tool_raises() -> None:
    with pytest.raises(ValueError, match="unknown_tool"):
        await _call_tool("not_a_tool", {})
