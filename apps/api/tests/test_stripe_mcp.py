"""Stripe MCP server — list_tools, list_charges, refund, error paths."""

from __future__ import annotations

import json

import pytest
from mcp_servers.stripe import data
from mcp_servers.stripe.server import _call_tool, _list_tools


@pytest.mark.asyncio
async def test_list_tools_publishes_three_tools() -> None:
    tools = await _list_tools()
    names = {t.name for t in tools}
    assert names == {"list_charges", "get_charge", "refund"}
    refund = next(t for t in tools if t.name == "refund")
    assert "charge_id" in refund.inputSchema["properties"]


@pytest.mark.asyncio
async def test_list_charges_returns_seeded_data() -> None:
    out = await _call_tool("list_charges", {"customer_id": "cus_demo_001"})
    payload = json.loads(out[0].text)
    assert any(c["id"] == "ch_001" for c in payload)
    # ch_003 is pre-refunded in seed data
    assert any(c["status"] == "refunded" for c in payload)


@pytest.mark.asyncio
async def test_refund_full_amount_marks_status_refunded() -> None:
    out = await _call_tool("refund", {"charge_id": "ch_001"})
    payload = json.loads(out[0].text)
    assert payload["status"] == "succeeded"
    assert payload["amount"] == 9900
    assert data.STORE.charges["ch_001"].status == "refunded"


@pytest.mark.asyncio
async def test_refund_already_refunded_raises() -> None:
    with pytest.raises(ValueError, match="already_refunded"):
        await _call_tool("refund", {"charge_id": "ch_003"})


@pytest.mark.asyncio
async def test_refund_amount_exceeding_remaining_raises() -> None:
    with pytest.raises(ValueError, match="invalid_refund_amount"):
        await _call_tool("refund", {"charge_id": "ch_001", "amount": 99999})


@pytest.mark.asyncio
async def test_unknown_tool_raises() -> None:
    with pytest.raises(ValueError, match="unknown_tool"):
        await _call_tool("not_a_tool", {})
