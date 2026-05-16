"""ToolBelt — unit tests (stub tools) + multi-server discovery smoke test."""

from __future__ import annotations

from typing import Any, ClassVar

import pytest
from langchain_core.tools import BaseTool
from resolveai_api.config import get_settings
from resolveai_api.mcp.toolbelt import ToolBelt


class _Stub(BaseTool):
    name: str = "stub"
    description: str = "stub"
    metadata: ClassVar[dict[str, Any]] = {}

    def _run(self, *args, **kwargs):
        return "ok"

    async def _arun(self, *args, **kwargs):
        return "ok"


def _make_tool(full_name: str, capability: str) -> BaseTool:
    class _T(_Stub):
        metadata: ClassVar[dict[str, Any]] = {
            "server": full_name.split(".")[0],
            "capability": capability,
            "full_name": full_name,
        }

    t = _T()
    t.name = full_name.replace(".", "_")
    return t


def test_for_agent_filters_by_full_name() -> None:
    belt = ToolBelt(
        [
            _make_tool("stripe.list_charges", "read"),
            _make_tool("stripe.refund", "destructive"),
            _make_tool("zendesk.update_ticket", "write"),
        ]
    )
    sliced = belt.for_agent(["stripe.refund", "zendesk.update_ticket"])
    names = {(t.metadata or {}).get("full_name") for t in sliced}
    assert names == {"stripe.refund", "zendesk.update_ticket"}


def test_by_capability_groups_correctly() -> None:
    belt = ToolBelt(
        [
            _make_tool("stripe.list_charges", "read"),
            _make_tool("stripe.refund", "destructive"),
            _make_tool("zendesk.update_ticket", "write"),
            _make_tool("zendesk.escalate", "destructive"),
        ]
    )
    assert {(t.metadata or {}).get("full_name") for t in belt.by_capability("destructive")} == {
        "stripe.refund",
        "zendesk.escalate",
    }
    assert len(belt.by_capability("write")) == 1
    assert len(belt.by_capability("read")) == 1


def test_manifest_contains_required_fields() -> None:
    belt = ToolBelt([_make_tool("stripe.refund", "destructive")])
    manifest = belt.manifest()
    assert manifest == [
        {
            "full_name": "stripe.refund",
            "server": "stripe",
            "capability": "destructive",
            "description": "stub",
        }
    ]


def test_contains_and_len() -> None:
    belt = ToolBelt([_make_tool("slack.notify_team", "write")])
    assert "slack.notify_team" in belt
    assert "slack.unknown" not in belt
    assert len(belt) == 1


@pytest.mark.asyncio
async def test_from_settings_discovers_all_five_servers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Smoke test: launch all 5 mock MCP servers and verify the belt aggregates them.

    Spawns 5 stdio subprocesses (`python -m mcp_servers.<name>`). Hermetic — no
    network, no LLM. Slowish (~1-2s) but worth the confidence: this is the
    single integration point M3 ships.
    """
    monkeypatch.setenv("MCP_STRIPE_CMD", "python -m mcp_servers.stripe")
    monkeypatch.setenv("MCP_ZENDESK_CMD", "python -m mcp_servers.zendesk")
    monkeypatch.setenv("MCP_SLACK_CMD", "python -m mcp_servers.slack")
    monkeypatch.setenv("MCP_SALESFORCE_CMD", "python -m mcp_servers.salesforce")
    monkeypatch.setenv("MCP_INTERCOM_CMD", "python -m mcp_servers.intercom")
    get_settings.cache_clear()

    belt = await ToolBelt.from_settings()

    full_names = {entry["full_name"] for entry in belt.manifest()}
    expected = {
        # Stripe (3 tools)
        "stripe.list_charges",
        "stripe.get_charge",
        "stripe.refund",
        # Zendesk (3 tools)
        "zendesk.get_ticket_history",
        "zendesk.update_ticket",
        "zendesk.escalate",
        # Slack (2 tools)
        "slack.notify_team",
        "slack.post_message",
        # Salesforce (2 tools)
        "salesforce.get_account",
        "salesforce.update_opportunity",
        # Intercom (2 tools)
        "intercom.get_conversation",
        "intercom.tag_user",
    }
    missing = expected - full_names
    assert not missing, f"missing tools in ToolBelt: {missing}"

    # Capabilities preserved end-to-end.
    by_full = {entry["full_name"]: entry["capability"] for entry in belt.manifest()}
    assert by_full["stripe.refund"] == "destructive"
    assert by_full["zendesk.escalate"] == "destructive"
    assert by_full["zendesk.update_ticket"] == "write"
    assert by_full["stripe.list_charges"] == "read"
