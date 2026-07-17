"""Human-in-the-Loop approval gate (M12).

High-risk (destructive-capability) tool calls are parked for human review before
they execute. The gate lives at the single `Executor.call_tool` chokepoint, so
every destructive action (`stripe.refund`, `zendesk.escalate`, …) is covered by
construction — no per-agent wiring.

Design (honest about the trade-off):
- **Store + request-scoped context**, not a nested-subgraph `interrupt()`. A
  destructive call with no decision yet is *parked*: the store records a pending
  `ApprovalRequest`, the executor returns a sentinel (does NOT run the tool), and
  the Supervisor emits an `awaiting_approval` SSE event instead of `done`.
- **Resume-by-replay**: the request id is deterministic in
  `(thread_ref, tool, args)`, so once a human approves via `POST /approvals/{id}`
  the client re-sends the ticket and the same destructive step now finds an
  `APPROVED` decision and executes. Durable conversation state is preserved by the
  existing `AsyncPostgresSaver` checkpointer. (In-place `Command(resume=...)`
  suspend/resume is the further-production step; see the plan doc.)

`APPROVAL_MODE=off` (default) makes the whole gate a no-op — byte-identical to the
pre-M12 path — so existing flows and tests are unchanged.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _request_id(thread_ref: str, tool: str, args: dict[str, Any]) -> str:
    """Deterministic id for a (thread, tool, args) triple → enables resume-by-replay."""
    canonical = json.dumps(args, sort_keys=True, default=str)
    digest = hashlib.sha256(f"{thread_ref}|{tool}|{canonical}".encode()).hexdigest()
    return digest[:16]


@dataclass
class ApprovalRequest:
    id: str
    thread_ref: str
    tenant_id: str
    tool: str
    capability: str
    args: dict[str, Any]
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: str = field(default_factory=_now)
    decided_at: str | None = None
    decided_by: str | None = None
    edited_args: dict[str, Any] | None = None
    note: str | None = None

    def effective_args(self) -> dict[str, Any]:
        """Args to actually execute with (human edits win on `edit`)."""
        return self.edited_args if self.edited_args is not None else self.args

    def to_public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "thread_ref": self.thread_ref,
            "tenant_id": self.tenant_id,
            "tool": self.tool,
            "capability": self.capability,
            "args": self.args,
            "status": str(self.status),
            "created_at": self.created_at,
            "decided_at": self.decided_at,
            "decided_by": self.decided_by,
            "edited_args": self.edited_args,
            "note": self.note,
        }


class ApprovalStore:
    """Process-global, thread-safe approval + takeover registry (in-memory).

    Production would back this with Postgres (durable, multi-replica); the API
    surface is deliberately storage-agnostic so that swap is local.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: dict[str, ApprovalRequest] = {}
        self._owners: dict[str, str] = {}  # thread_ref -> human owner (takeover)

    # ---- approvals ----
    def require(
        self,
        *,
        thread_ref: str,
        tenant_id: str,
        tool: str,
        capability: str,
        args: dict[str, Any],
    ) -> ApprovalRequest:
        """Get-or-create the approval request for this destructive call."""
        rid = _request_id(thread_ref, tool, args)
        with self._lock:
            existing = self._requests.get(rid)
            if existing is not None:
                return existing
            request = ApprovalRequest(
                id=rid,
                thread_ref=thread_ref,
                tenant_id=tenant_id,
                tool=tool,
                capability=capability,
                args=dict(args),
            )
            self._requests[rid] = request
            return request

    def decide(
        self,
        request_id: str,
        *,
        decision: str,
        by: str | None = None,
        edited_args: dict[str, Any] | None = None,
        note: str | None = None,
    ) -> ApprovalRequest | None:
        """Record a human decision. `decision` ∈ approve | deny | edit."""
        normalized = decision.strip().lower()
        with self._lock:
            request = self._requests.get(request_id)
            if request is None:
                return None
            if normalized in ("approve", "approved", "edit", "edited"):
                request.status = ApprovalStatus.APPROVED
                if normalized in ("edit", "edited") and edited_args is not None:
                    request.edited_args = dict(edited_args)
            elif normalized in ("deny", "denied", "reject", "rejected"):
                request.status = ApprovalStatus.DENIED
            else:
                raise ValueError(f"unknown decision: {decision!r}")
            request.decided_at = _now()
            request.decided_by = by
            request.note = note
            return request

    def get(self, request_id: str) -> ApprovalRequest | None:
        return self._requests.get(request_id)

    def list(
        self, *, tenant_id: str | None = None, status: str | None = None
    ) -> list[ApprovalRequest]:
        with self._lock:
            items = list(self._requests.values())
        if tenant_id is not None:
            items = [r for r in items if r.tenant_id == tenant_id]
        if status is not None:
            items = [r for r in items if str(r.status) == status]
        return sorted(items, key=lambda r: r.created_at)

    # ---- takeover (agent → human) ----
    def set_owner(self, thread_ref: str, owner: str) -> None:
        with self._lock:
            self._owners[thread_ref] = owner

    def owner(self, thread_ref: str) -> str | None:
        return self._owners.get(thread_ref)

    def release(self, thread_ref: str) -> None:
        with self._lock:
            self._owners.pop(thread_ref, None)

    def is_human_owned(self, thread_ref: str) -> bool:
        return thread_ref in self._owners

    def reset(self) -> None:
        """Test hook: clear all state."""
        with self._lock:
            self._requests.clear()
            self._owners.clear()


_STORE = ApprovalStore()


def get_approval_store() -> ApprovalStore:
    return _STORE


# --------------------------------------------------------------------------- #
# Request-scoped approval context (mirrors core.usage.capture_run)
# --------------------------------------------------------------------------- #


@dataclass
class ApprovalContext:
    thread_ref: str
    tenant_id: str
    enabled: bool
    pending: list[ApprovalRequest] = field(default_factory=list)


_ctx: contextvars.ContextVar[ApprovalContext | None] = contextvars.ContextVar(
    "resolveai_approval_ctx", default=None
)


def current_approval_context() -> ApprovalContext | None:
    return _ctx.get()


@contextmanager
def approval_context(
    *, thread_ref: str, tenant_id: str, enabled: bool
) -> Iterator[ApprovalContext]:
    ctx = ApprovalContext(thread_ref=thread_ref, tenant_id=tenant_id, enabled=enabled)
    token = _ctx.set(ctx)
    try:
        yield ctx
    finally:
        _ctx.reset(token)


def resolve_approval_enabled(mode: object, env_profile: object = "demo") -> bool:
    """`destructive`/`on` → gate destructive tools; `auto` → follow profile
    (production → on); anything else (incl. `off`/unset) → disabled."""
    value = str(mode).strip().lower()
    if value in ("destructive", "on", "true", "1", "yes"):
        return True
    if value == "auto":
        return str(env_profile).strip().lower() == "production"
    return False
