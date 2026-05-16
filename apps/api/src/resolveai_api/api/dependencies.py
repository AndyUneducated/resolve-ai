"""Shared FastAPI dependencies."""

from __future__ import annotations

from fastapi import Request

from resolveai_api.agents.supervisor import SupervisorGraph


def get_supervisor(request: Request) -> SupervisorGraph:
    """Return the SupervisorGraph wired in `main.lifespan`."""
    supervisor: SupervisorGraph | None = getattr(request.app.state, "supervisor", None)
    if supervisor is None:  # pragma: no cover — guards misconfigured tests
        raise RuntimeError("SupervisorGraph not initialised; lifespan did not run.")
    return supervisor
