"""M12 — Human-in-the-Loop approval gate + takeover.

All LM-free: the gate lives at the `Executor` chokepoint, so we exercise it with
fake destructive tools and the deterministic Escalation agent. No Ollama.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any, ClassVar

import pytest
from fastapi.testclient import TestClient
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import MemorySaver
from resolveai_api.agents.escalation import EscalationAgent
from resolveai_api.agents.state import GraphState
from resolveai_api.agents.supervisor import SupervisorGraph
from resolveai_api.config import get_settings
from resolveai_api.core.approvals import (
    ApprovalStatus,
    approval_context,
    get_approval_store,
    resolve_approval_enabled,
)
from resolveai_api.core.executor import Executor
from resolveai_api.guardrails.memory_isolator import MemoryIsolator
from resolveai_api.main import create_app


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    get_approval_store().reset()
    for layer in ("GUARDRAIL_L1", "GUARDRAIL_L2", "GUARDRAIL_L3", "GUARDRAIL_L4"):
        monkeypatch.setenv(layer, "off")
    monkeypatch.setenv("LLM_BACKEND", "fake")
    get_settings.cache_clear()
    yield
    get_approval_store().reset()
    get_settings.cache_clear()


class _FakeRefund(BaseTool):
    name: str = "stripe_refund"
    description: str = "fake destructive refund"
    metadata: ClassVar[dict[str, Any]] = {
        "server": "stripe",
        "capability": "destructive",
        "full_name": "stripe.refund",
    }

    def _run(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"refunded": kwargs}

    async def _arun(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"refunded": kwargs}


class _FakeZendeskEscalate(BaseTool):
    name: str = "zendesk_escalate"
    description: str = "fake destructive escalate"
    metadata: ClassVar[dict[str, Any]] = {
        "server": "zendesk",
        "capability": "destructive",
        "full_name": "zendesk.escalate",
    }

    def _run(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"id": kwargs.get("ticket_id"), "status": "escalated"}

    async def _arun(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"id": kwargs.get("ticket_id"), "status": "escalated"}


# --------------------------- pure store ---------------------------


def test_require_is_deterministic_and_dedupes() -> None:
    store = get_approval_store()
    a = store.require(
        thread_ref="ns", tenant_id="demo", tool="stripe.refund",
        capability="destructive", args={"amount": 10, "charge": "ch_1"},
    )
    b = store.require(
        thread_ref="ns", tenant_id="demo", tool="stripe.refund",
        capability="destructive", args={"charge": "ch_1", "amount": 10},  # key order flipped
    )
    c = store.require(
        thread_ref="ns", tenant_id="demo", tool="stripe.refund",
        capability="destructive", args={"amount": 999, "charge": "ch_1"},
    )
    assert a.id == b.id  # same (thread, tool, args) → same id (order-insensitive)
    assert a is b
    assert a.id != c.id  # different args → different request


def test_decide_approve_deny_edit_and_audit() -> None:
    store = get_approval_store()
    req = store.require(
        thread_ref="ns", tenant_id="demo", tool="stripe.refund",
        capability="destructive", args={"amount": 50},
    )
    assert req.status is ApprovalStatus.PENDING

    approved = store.decide(req.id, decision="approve", by="alice", note="ok")
    assert approved is not None
    assert approved.status is ApprovalStatus.APPROVED
    assert approved.decided_by == "alice" and approved.decided_at is not None
    assert approved.note == "ok"

    denied = store.decide(req.id, decision="deny", by="bob")
    assert denied is not None and denied.status is ApprovalStatus.DENIED

    edited = store.decide(req.id, decision="edit", edited_args={"amount": 5})
    assert edited is not None and edited.status is ApprovalStatus.APPROVED
    assert edited.effective_args() == {"amount": 5}

    assert store.decide("does-not-exist", decision="approve") is None
    with pytest.raises(ValueError):
        store.decide(req.id, decision="maybe")


def test_list_filters_by_tenant_and_status() -> None:
    store = get_approval_store()
    store.require(thread_ref="a", tenant_id="t1", tool="stripe.refund",
                  capability="destructive", args={"n": 1})
    r2 = store.require(thread_ref="b", tenant_id="t2", tool="stripe.refund",
                       capability="destructive", args={"n": 2})
    store.decide(r2.id, decision="approve")

    assert len(store.list(tenant_id="t1")) == 1
    assert len(store.list(status="pending")) == 1
    assert len(store.list(status="approved")) == 1
    assert len(store.list()) == 2


def test_takeover_owner_release() -> None:
    store = get_approval_store()
    assert store.is_human_owned("ns") is False
    store.set_owner("ns", "agent-42")
    assert store.is_human_owned("ns") is True
    assert store.owner("ns") == "agent-42"
    store.release("ns")
    assert store.is_human_owned("ns") is False


@pytest.mark.parametrize(
    ("mode", "profile", "expected"),
    [
        ("off", "demo", False),
        ("off", "production", False),
        ("destructive", "demo", True),
        ("on", "demo", True),
        ("auto", "demo", False),
        ("auto", "production", True),
        ("", "demo", False),
    ],
)
def test_resolve_approval_enabled(mode: str, profile: str, expected: bool) -> None:
    assert resolve_approval_enabled(mode, profile) is expected


# --------------------------- executor gate ---------------------------


@pytest.mark.asyncio
async def test_gate_noop_when_disabled() -> None:
    executor = Executor()
    tool = _FakeRefund()
    with approval_context(thread_ref="ns", tenant_id="demo", enabled=False):
        result = await executor.call_tool(
            tool=tool, args={"amount": 10}, whitelist=["stripe.refund"]
        )
    assert result.approval == "none"
    assert result.output == {"refunded": {"amount": 10}}
    assert get_approval_store().list() == []  # nothing parked


@pytest.mark.asyncio
async def test_gate_parks_then_denies_then_approves() -> None:
    executor = Executor()
    tool = _FakeRefund()
    args = {"amount": 200, "charge": "ch_9"}

    # 1) no decision yet → parked, tool NOT executed
    with approval_context(thread_ref="ns", tenant_id="demo", enabled=True) as ctx:
        parked = await executor.call_tool(tool=tool, args=args, whitelist=["stripe.refund"])
        assert parked.approval == "pending"
        assert "awaiting human approval" in str(parked.output)
        assert len(ctx.pending) == 1
    request_id = ctx.pending[0].id

    # 2) deny → blocked, still not executed
    get_approval_store().decide(request_id, decision="deny", by="alice")
    with approval_context(thread_ref="ns", tenant_id="demo", enabled=True):
        denied = await executor.call_tool(tool=tool, args=args, whitelist=["stripe.refund"])
    assert denied.approval == "denied"
    assert "denied" in str(denied.output)

    # 3) approve → executes for real
    get_approval_store().decide(request_id, decision="approve", by="alice")
    with approval_context(thread_ref="ns", tenant_id="demo", enabled=True):
        ran = await executor.call_tool(tool=tool, args=args, whitelist=["stripe.refund"])
    assert ran.approval == "none"
    assert ran.output == {"refunded": args}


@pytest.mark.asyncio
async def test_gate_edit_executes_with_edited_args() -> None:
    executor = Executor()
    tool = _FakeRefund()
    original = {"amount": 999, "charge": "ch_1"}
    req = get_approval_store().require(
        thread_ref="ns", tenant_id="demo", tool="stripe.refund",
        capability="destructive", args=original,
    )
    get_approval_store().decide(
        req.id, decision="edit", edited_args={"amount": 1, "charge": "ch_1"}
    )
    with approval_context(thread_ref="ns", tenant_id="demo", enabled=True):
        ran = await executor.call_tool(tool=tool, args=original, whitelist=["stripe.refund"])
    assert ran.approval == "none"
    assert ran.output == {"refunded": {"amount": 1, "charge": "ch_1"}}  # human-edited


# --------------------------- e2e via escalation agent ---------------------------


@pytest.mark.asyncio
async def test_escalation_parks_destructive_then_resumes_on_approval() -> None:
    agent = EscalationAgent.default(tools=[_FakeZendeskEscalate()], executor=Executor())
    ns = MemoryIsolator.namespace("demo", "cus_1", "t-esc")
    state: GraphState = {
        "messages": [],
        "tenant_id": "demo",
        "customer_id": "cus_1",
        "thread_id": "t-esc",
        "tool_calls": [],
        "ticket_summary": {"intent": "billing", "entities": {"ticket_id": "zd_9"}},
    }

    with approval_context(thread_ref=ns, tenant_id="demo", enabled=True):
        first = await agent.run(state)
    escalate_obs = next(
        tc for tc in first["tool_calls"] if tc.get("step") == "zendesk.escalate"
    )["observation"]
    assert "awaiting human approval" in escalate_obs

    pending = get_approval_store().list(status="pending")
    parked = [p for p in pending if p.tool == "zendesk.escalate"]
    assert len(parked) == 1

    get_approval_store().decide(parked[0].id, decision="approve", by="reviewer")
    with approval_context(thread_ref=ns, tenant_id="demo", enabled=True):
        second = await agent.run(state)
    escalate_obs2 = next(
        tc for tc in second["tool_calls"] if tc.get("step") == "zendesk.escalate"
    )["observation"]
    assert "escalated" in escalate_obs2.lower()


@pytest.mark.asyncio
async def test_supervisor_short_circuits_when_thread_is_human_owned() -> None:
    ns = MemoryIsolator.namespace("demo", "cus_1", "t-owned")
    get_approval_store().set_owner(ns, "human-agent-7")
    supervisor = SupervisorGraph(checkpointer=MemorySaver(), mcp_tools=[])
    events = [
        evt
        async for evt in supervisor.stream(
            message="hi", customer_id="cus_1", tenant_id="demo", thread_id="t-owned"
        )
    ]
    assert len(events) == 1
    assert events[0]["type"] == "human_owned"
    assert json.loads(events[0]["data"])["owner"] == "human-agent-7"


# --------------------------- API ---------------------------


def test_approvals_api_roundtrip() -> None:
    store = get_approval_store()
    req = store.require(
        thread_ref="demo::cus_1::t-1", tenant_id="demo", tool="stripe.refund",
        capability="destructive", args={"amount": 42},
    )
    client = TestClient(create_app())

    listed = client.get("/api/v1/approvals", params={"status": "pending"})
    assert listed.status_code == 200
    assert any(item["id"] == req.id for item in listed.json())

    decided = client.post(f"/api/v1/approvals/{req.id}", json={"decision": "approve", "by": "op"})
    assert decided.status_code == 200
    assert decided.json()["status"] == "approved"

    got = client.get(f"/api/v1/approvals/{req.id}")
    assert got.status_code == 200 and got.json()["decided_by"] == "op"

    assert client.get("/api/v1/approvals/nope").status_code == 404
    assert client.post(f"/api/v1/approvals/{req.id}", json={"decision": "??"}).status_code == 400


def test_takeover_api() -> None:
    client = TestClient(create_app())
    resp = client.post(
        "/api/v1/threads/takeover",
        json={"tenant_id": "demo", "customer_id": "cus_1", "thread_id": "t-1", "owner": "op"},
    )
    assert resp.status_code == 200
    thread_ref = resp.json()["thread_ref"]
    assert get_approval_store().is_human_owned(thread_ref) is True

    released = client.post(
        "/api/v1/threads/release",
        json={"tenant_id": "demo", "customer_id": "cus_1", "thread_id": "t-1"},
    )
    assert released.status_code == 200
    assert get_approval_store().is_human_owned(thread_ref) is False
