"""Salesforce MCP server — account fetch, opportunity update, error paths."""

from __future__ import annotations

import json

import pytest
from mcp_servers.salesforce import data
from mcp_servers.salesforce.server import _call_tool, _list_tools


@pytest.mark.asyncio
async def test_list_tools_publishes_two_tools() -> None:
    tools = await _list_tools()
    names = {t.name for t in tools}
    assert names == {"get_account", "update_opportunity"}


@pytest.mark.asyncio
async def test_get_account_returns_seeded_tier() -> None:
    out = await _call_tool("get_account", {"customer_id": "cus_demo_003"})
    payload = json.loads(out[0].text)
    assert payload["sla_tier"] == "enterprise"
    assert payload["name"] == "MegaCorp Inc."


@pytest.mark.asyncio
async def test_get_account_unknown_raises() -> None:
    with pytest.raises(KeyError, match="account_not_found"):
        await _call_tool("get_account", {"customer_id": "ghost"})


@pytest.mark.asyncio
async def test_update_opportunity_stage_and_amount() -> None:
    out = await _call_tool(
        "update_opportunity",
        {"opportunity_id": "op_001", "stage": "closed_won", "amount": 30000},
    )
    payload = json.loads(out[0].text)
    assert payload["stage"] == "closed_won"
    assert payload["amount"] == 30000
    assert data.STORE.opportunities["op_001"].stage == "closed_won"


@pytest.mark.asyncio
async def test_update_opportunity_requires_change() -> None:
    with pytest.raises(ValueError, match="update_requires_stage_or_amount"):
        await _call_tool("update_opportunity", {"opportunity_id": "op_001"})


@pytest.mark.asyncio
async def test_update_opportunity_rejects_invalid_stage() -> None:
    with pytest.raises(ValueError, match="invalid_stage"):
        await _call_tool(
            "update_opportunity", {"opportunity_id": "op_001", "stage": "exploded"}
        )


@pytest.mark.asyncio
async def test_unknown_tool_raises() -> None:
    with pytest.raises(ValueError, match="unknown_tool"):
        await _call_tool("not_a_tool", {})
