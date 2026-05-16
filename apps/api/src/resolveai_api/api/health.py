"""Liveness / readiness probe."""

from __future__ import annotations

from fastapi import APIRouter

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
    return {"status": "ok"}


@router.get("/readyz")
async def readyz() -> dict[str, object]:
    # TODO: 接 DB ping / MCP client ping
    return {"status": "ok", "checks": {"db": "todo", "mcp": "todo"}}
