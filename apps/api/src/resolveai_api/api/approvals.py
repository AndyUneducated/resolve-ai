"""Human-in-the-Loop approval + takeover API (M12).

Operators use these endpoints to drain the review queue and take over threads:

- ``GET  /approvals``                 — pending (or filtered) review queue
- ``GET  /approvals/{id}``            — one request (with its audit fields)
- ``POST /approvals/{id}``            — approve / deny / edit a parked action
- ``POST /threads/takeover``          — a human agent takes over a thread
- ``POST /threads/release``           — hand the thread back to automation

The decision is written to the process-global `ApprovalStore`; the ticket then
resumes by replay (client re-sends the message → the destructive step now finds
an APPROVED decision and executes). See `core/approvals.py` for the rationale.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from resolveai_api.core.approvals import get_approval_store
from resolveai_api.guardrails.memory_isolator import MemoryIsolator

router = APIRouter(tags=["approvals"])


class DecisionBody(BaseModel):
    decision: str = Field(description="approve | deny | edit")
    by: str | None = Field(default=None, description="reviewer identity (audit)")
    edited_args: dict[str, Any] | None = Field(
        default=None, description="on `edit`: the args to execute with instead"
    )
    note: str | None = Field(default=None, description="free-text audit note")


class TakeoverBody(BaseModel):
    tenant_id: str = "demo"
    customer_id: str
    thread_id: str = "default"
    owner: str = Field(description="human agent taking over the thread")


class ReleaseBody(BaseModel):
    tenant_id: str = "demo"
    customer_id: str
    thread_id: str = "default"


@router.get("/approvals")
async def list_approvals(
    tenant_id: str | None = None, status: str | None = None
) -> list[dict[str, Any]]:
    store = get_approval_store()
    return [r.to_public() for r in store.list(tenant_id=tenant_id, status=status)]


@router.get("/approvals/{approval_id}")
async def get_approval(approval_id: str) -> dict[str, Any]:
    request = get_approval_store().get(approval_id)
    if request is None:
        raise HTTPException(status_code=404, detail="approval not found")
    return request.to_public()


@router.post("/approvals/{approval_id}")
async def decide_approval(approval_id: str, body: DecisionBody) -> dict[str, Any]:
    store = get_approval_store()
    try:
        request = store.decide(
            approval_id,
            decision=body.decision,
            by=body.by,
            edited_args=body.edited_args,
            note=body.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if request is None:
        raise HTTPException(status_code=404, detail="approval not found")
    return request.to_public()


@router.post("/threads/takeover")
async def takeover_thread(body: TakeoverBody) -> dict[str, Any]:
    thread_ref = MemoryIsolator.namespace(body.tenant_id, body.customer_id, body.thread_id)
    get_approval_store().set_owner(thread_ref, body.owner)
    return {"thread_ref": thread_ref, "owner": body.owner}


@router.post("/threads/release")
async def release_thread(body: ReleaseBody) -> dict[str, Any]:
    thread_ref = MemoryIsolator.namespace(body.tenant_id, body.customer_id, body.thread_id)
    get_approval_store().release(thread_ref)
    return {"thread_ref": thread_ref, "released": True}
