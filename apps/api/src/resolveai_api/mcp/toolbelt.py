"""ToolBelt — single source of truth for MCP-backed LangChain tools.

行业对齐（2026）：MCP discovery + LangChain `BaseTool` 桥接是事实标准；本类
把 [`mcp/loader.py`](loader.py) 的发现/标注流程封装为可注入对象，并提供
per-agent slicing 与 JSON manifest（给 /admin / ablation log 用）。

Why a class instead of bare functions:
- One place to compute & cache the capability map (decision 4 · Layer 2).
- Agents depend on `ToolBelt`, not on the loader internals; swapping mock MCP
  servers for real ones later does not touch any agent.
- `manifest()` becomes the contract surface for future admin / observability
  endpoints (M4 / M5 / M7).
"""

from __future__ import annotations

import logging
from typing import Literal

from langchain_core.tools import BaseTool

from resolveai_api.mcp.loader import build_client, load_tools

logger = logging.getLogger(__name__)

Capability = Literal["read", "write", "destructive"]


class ToolBelt:
    """Holds the discovered MCP tools and exposes per-agent / per-capability views."""

    def __init__(self, tools: list[BaseTool]) -> None:
        self._tools = list(tools)
        self._by_full_name: dict[str, BaseTool] = {
            (t.metadata or {}).get("full_name", t.name): t for t in self._tools
        }

    @classmethod
    async def from_settings(cls) -> ToolBelt:
        """Discover all MCP tools declared in the registry; tolerate partial failures."""
        try:
            client = build_client()
            tools = await load_tools(client)
        except Exception:  # pragma: no cover — defensive
            logger.exception("ToolBelt discovery failed; returning empty belt")
            tools = []
        return cls(tools)

    # --- views -----------------------------------------------------------

    @property
    def tools(self) -> list[BaseTool]:
        return list(self._tools)

    def for_agent(self, whitelist: list[str]) -> list[BaseTool]:
        """Slice to tools whose `metadata.full_name` appears in `whitelist`."""
        allowed = set(whitelist)
        return [t for t in self._tools if (t.metadata or {}).get("full_name") in allowed]

    def by_capability(self, capability: Capability) -> list[BaseTool]:
        return [
            t
            for t in self._tools
            if (t.metadata or {}).get("capability") == capability
        ]

    # --- introspection ---------------------------------------------------

    def manifest(self) -> list[dict[str, str]]:
        """Serializable summary; used by future /admin and ablation logging."""
        out: list[dict[str, str]] = []
        for t in self._tools:
            meta = t.metadata or {}
            out.append(
                {
                    "full_name": str(meta.get("full_name", t.name)),
                    "server": str(meta.get("server", "")),
                    "capability": str(meta.get("capability", "read")),
                    "description": (t.description or "").strip(),
                }
            )
        return out

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, full_name: object) -> bool:
        return isinstance(full_name, str) and full_name in self._by_full_name
