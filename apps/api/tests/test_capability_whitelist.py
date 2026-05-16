"""Capability whitelist enforcement (decision 4 · Layer 2).

Three-tier policy (M3):
- read         → default allow (even with empty whitelist)
- write        → must appear in whitelist
- destructive  → must appear in whitelist; result.audit=True
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest
from langchain_core.tools import BaseTool
from resolveai_api.core.executor import Executor


class _NoopRefund(BaseTool):
    name: str = "stripe_refund"
    description: str = "stub destructive tool"
    metadata: ClassVar[dict[str, Any]] = {
        "server": "stripe",
        "capability": "destructive",
        "full_name": "stripe.refund",
    }

    def _run(self, *args, **kwargs):
        return "ok"

    async def _arun(self, *args, **kwargs):
        return "ok"


class _NoopList(BaseTool):
    name: str = "stripe_list_charges"
    description: str = "stub read tool"
    metadata: ClassVar[dict[str, Any]] = {
        "server": "stripe",
        "capability": "read",
        "full_name": "stripe.list_charges",
    }

    def _run(self, *args, **kwargs):
        return "ok"

    async def _arun(self, *args, **kwargs):
        return [{"id": "ch_x"}]


class _NoopUpdateTicket(BaseTool):
    name: str = "zendesk_update_ticket"
    description: str = "stub write tool"
    metadata: ClassVar[dict[str, Any]] = {
        "server": "zendesk",
        "capability": "write",
        "full_name": "zendesk.update_ticket",
    }

    def _run(self, *args, **kwargs):
        return "ok"

    async def _arun(self, *args, **kwargs):
        return {"id": "zd_001", "status": "pending"}


@pytest.mark.asyncio
async def test_destructive_tool_blocked_when_not_in_whitelist() -> None:
    executor = Executor()
    with pytest.raises(PermissionError, match=r"stripe\.refund"):
        await executor.call_tool(
            tool=_NoopRefund(),
            args={"charge_id": "ch_001"},
            whitelist=["stripe.list_charges"],  # refund deliberately missing
        )


@pytest.mark.asyncio
async def test_destructive_tool_allowed_when_granted_and_audited() -> None:
    executor = Executor()
    result = await executor.call_tool(
        tool=_NoopRefund(),
        args={"charge_id": "ch_001"},
        whitelist=["stripe.refund"],
    )
    assert result.tool == "stripe.refund"
    assert result.capability == "destructive"
    assert result.audit is True


@pytest.mark.asyncio
async def test_read_tool_allowed_even_outside_whitelist() -> None:
    """Reads are non-destructive and default to allowed."""
    executor = Executor()
    result = await executor.call_tool(
        tool=_NoopList(),
        args={"customer_id": "cus_demo_001"},
        whitelist=[],  # empty whitelist
    )
    assert result.tool == "stripe.list_charges"
    assert result.audit is False


@pytest.mark.asyncio
async def test_write_tool_blocked_when_not_in_whitelist() -> None:
    executor = Executor()
    with pytest.raises(PermissionError, match=r"zendesk\.update_ticket"):
        await executor.call_tool(
            tool=_NoopUpdateTicket(),
            args={"ticket_id": "zd_001", "status": "pending"},
            whitelist=["zendesk.get_ticket_history"],  # write deliberately missing
        )


@pytest.mark.asyncio
async def test_write_tool_allowed_when_granted() -> None:
    executor = Executor()
    result = await executor.call_tool(
        tool=_NoopUpdateTicket(),
        args={"ticket_id": "zd_001", "status": "pending"},
        whitelist=["zendesk.update_ticket"],
    )
    assert result.tool == "zendesk.update_ticket"
    assert result.capability == "write"
    assert result.audit is False  # only destructive triggers audit
