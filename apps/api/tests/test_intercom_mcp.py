"""Intercom MCP server — conversation fetch, tag user, error paths."""

from __future__ import annotations

import json

import pytest
from mcp_servers.intercom import data
from mcp_servers.intercom.server import _call_tool, _list_tools


@pytest.mark.asyncio
async def test_list_tools_publishes_two_tools() -> None:
    tools = await _list_tools()
    names = {t.name for t in tools}
    assert names == {"get_conversation", "tag_user"}


@pytest.mark.asyncio
async def test_get_conversation_returns_messages() -> None:
    out = await _call_tool("get_conversation", {"conversation_id": "ic_001"})
    payload = json.loads(out[0].text)
    assert payload["id"] == "ic_001"
    assert any("502" in m for m in payload["messages"])


@pytest.mark.asyncio
async def test_get_conversation_unknown_raises() -> None:
    with pytest.raises(KeyError, match="conversation_not_found"):
        await _call_tool("get_conversation", {"conversation_id": "ghost"})


@pytest.mark.asyncio
async def test_tag_user_appends() -> None:
    out = await _call_tool(
        "tag_user", {"user_id": "u_demo_002", "tag": "needs-followup"}
    )
    payload = json.loads(out[0].text)
    assert "needs-followup" in payload["tags"]
    assert "needs-followup" in data.STORE.users["u_demo_002"].tags


@pytest.mark.asyncio
async def test_tag_user_idempotent_failure() -> None:
    await _call_tool("tag_user", {"user_id": "u_demo_002", "tag": "vip"})
    with pytest.raises(ValueError, match="already_tagged"):
        await _call_tool("tag_user", {"user_id": "u_demo_002", "tag": "vip"})


@pytest.mark.asyncio
async def test_tag_user_unknown_user_raises() -> None:
    with pytest.raises(KeyError, match="user_not_found"):
        await _call_tool("tag_user", {"user_id": "ghost", "tag": "x"})


@pytest.mark.asyncio
async def test_unknown_tool_raises() -> None:
    with pytest.raises(ValueError, match="unknown_tool"):
        await _call_tool("not_a_tool", {})
