"""Liveness / readiness probe."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import text

router = APIRouter(tags=["health"])


@router.get("/")
async def root() -> dict[str, str]:
    return {
        "name": "resolveai-api",
        "status": "ok",
        "docs": "/docs",
    }


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness: the process is up. Cheap, never touches dependencies."""
    return {"status": "ok"}


async def _ping_db(timeout_s: float = 2.0) -> str:
    """Best-effort `SELECT 1` against the app DB; never raises."""

    async def _run() -> None:
        from resolveai_api.retrieval.store import get_engine

        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    try:
        await asyncio.wait_for(_run(), timeout=timeout_s)
        return "ok"
    except Exception as exc:  # unreachable DB / auth / timeout — report, don't crash
        return f"down: {type(exc).__name__}"


def _check_mcp(request: Request) -> str:
    toolbelt = getattr(request.app.state, "toolbelt", None)
    try:
        if toolbelt is not None and hasattr(toolbelt, "tools"):
            n = len(toolbelt.tools)
        else:
            n = len(getattr(request.app.state, "mcp_tools", []) or [])
    except Exception as exc:
        return f"error: {type(exc).__name__}"
    return f"ok ({n} tools)" if n > 0 else "no_tools"


@router.get("/readyz")
async def readyz(request: Request, response: Response) -> dict[str, object]:
    """Readiness: verify the app can actually serve (DB reachable, MCP discovered).

    Returns 503 when degraded so orchestrators (K8s) hold traffic until ready.
    """
    checks = {
        "db": await _ping_db(),
        "mcp": _check_mcp(request),
    }
    ready = checks["db"] == "ok" and checks["mcp"].startswith("ok")
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if ready else "degraded", "checks": checks}
