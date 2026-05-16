"""Slack MCP server — notify, post, mention parsing, error paths."""

from __future__ import annotations

import json

import pytest
from mcp_servers.slack import data
from mcp_servers.slack.server import _call_tool, _list_tools


@pytest.mark.asyncio
async def test_list_tools_publishes_two_tools() -> None:
    tools = await _list_tools()
    names = {t.name for t in tools}
    assert names == {"notify_team", "post_message"}


@pytest.mark.asyncio
async def test_notify_team_renders_mention_prefix() -> None:
    out = await _call_tool(
        "notify_team",
        {
            "channel": "#oncall-billing",
            "message": "duplicate refund needed",
            "mention": "@oncall",
        },
    )
    payload = json.loads(out[0].text)
    assert payload["mention"] == "@oncall"
    assert payload["text"].startswith("@oncall ")
    assert "duplicate refund needed" in payload["text"]
    # Persisted in store
    assert any(m.channel == "#oncall-billing" for m in data.STORE.messages)


@pytest.mark.asyncio
async def test_post_message_without_mention() -> None:
    out = await _call_tool(
        "post_message", {"channel": "#general", "message": "FYI: new incident"}
    )
    payload = json.loads(out[0].text)
    assert payload["mention"] is None
    assert payload["text"] == "FYI: new incident"


@pytest.mark.asyncio
async def test_notify_unknown_channel_raises() -> None:
    with pytest.raises(KeyError, match="channel_not_found"):
        await _call_tool(
            "notify_team", {"channel": "#nope", "message": "hi"}
        )


@pytest.mark.asyncio
async def test_notify_invalid_channel_shape_raises() -> None:
    with pytest.raises(ValueError, match="invalid_channel"):
        await _call_tool(
            "notify_team", {"channel": "oncall", "message": "hi"}
        )


@pytest.mark.asyncio
async def test_notify_invalid_mention_raises() -> None:
    with pytest.raises(ValueError, match="invalid_mention"):
        await _call_tool(
            "notify_team",
            {"channel": "#oncall-billing", "message": "hi", "mention": "oncall"},
        )


@pytest.mark.asyncio
async def test_unknown_tool_raises() -> None:
    with pytest.raises(ValueError, match="unknown_tool"):
        await _call_tool("not_a_tool", {})
