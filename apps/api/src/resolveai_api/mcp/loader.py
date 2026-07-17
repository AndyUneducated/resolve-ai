"""MCP loader — adapt MCP servers to LangChain `BaseTool` via the official adapter.

行业对齐（2026）：用 `langchain-mcp-adapters` 的 `MultiServerMCPClient` 自动把
每个 MCP server 暴露的工具转换成 LangChain `BaseTool`，不再自己手写 schema bridge。

每个 tool 上 `metadata["server"]` 与 `metadata["capability"]` 由本模块打标，
[`core/executor.py`](../core/executor.py) 据此强制 capability whitelist（决策 4 · Layer 2）。
"""

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING, Any

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.sessions import StdioConnection

from resolveai_api.config import get_settings
from resolveai_api.guardrails.attribution import flag_enabled
from resolveai_api.mcp.registry import McpServerSpec, default_servers

if TYPE_CHECKING:
    pass

# capability 元数据来自每个 mock server 的 TOOLS 列表
_CAPABILITY_REGISTRY: dict[str, dict[str, str]] = {}


def _load_capability_map() -> dict[str, dict[str, str]]:
    """Lazy-load capability metadata from each mcp_servers.<name> package.

    Result: {"stripe": {"refund": "destructive", "list_charges": "read", ...}, ...}
    """
    global _CAPABILITY_REGISTRY
    if _CAPABILITY_REGISTRY:
        return _CAPABILITY_REGISTRY

    servers: dict[str, dict[str, str]] = {}
    for spec in default_servers():
        try:
            module = __import__(
                f"mcp_servers.{spec.name}.server",
                fromlist=["TOOLS"],
            )
            tools_meta: list[dict[str, Any]] = getattr(module, "TOOLS", [])
            servers[spec.name] = {
                t["name"]: t.get("capability", "read") for t in tools_meta
            }
        except ImportError:
            servers[spec.name] = {}
    _CAPABILITY_REGISTRY = servers
    return servers


def _spec_to_connection(spec: McpServerSpec) -> StdioConnection:
    """Convert McpServerSpec.cmd ('python -m mcp_servers.stripe') to StdioConnection."""
    parts = shlex.split(spec.cmd)
    if not parts:
        raise ValueError(f"empty cmd for MCP server {spec.name!r}")
    command, *args = _sandbox_wrap(parts)
    return StdioConnection(transport="stdio", command=command, args=args)


def _sandbox_wrap(parts: list[str]) -> list[str]:
    settings = get_settings()
    if not flag_enabled(getattr(settings, "guardrail_l2", "on")):
        return parts
    mode = str(getattr(settings, "sandbox_mode", "off")).strip().lower()
    if mode == "off":
        return parts

    image = str(getattr(settings, "mcp_sandbox_image", "resolveai/mcp-servers:dev"))
    docker_cmd = [
        "docker",
        "run",
        "--rm",
        "-i",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--pids-limit=64",
        "--memory=512m",
    ]
    if mode == "gvisor":
        docker_cmd.extend(["--runtime", settings.gvisor_runtime])
    docker_cmd.append(image)
    docker_cmd.extend(parts)
    return docker_cmd


def build_client(servers: list[McpServerSpec] | None = None) -> MultiServerMCPClient:
    """Build a MultiServerMCPClient from registry specs (filtered to stdio for now)."""
    specs = servers or default_servers()
    # dict[str, Any]: MultiServerMCPClient expects a union of connection types;
    # we only build StdioConnection, so widen to Any to satisfy the invariant param.
    connections: dict[str, Any] = {
        spec.name: _spec_to_connection(spec)
        for spec in specs
        if spec.transport == "stdio"
    }
    return MultiServerMCPClient(connections=connections, tool_name_prefix=True)


def _annotate(tool: BaseTool, server: str, tool_name: str) -> BaseTool:
    """Stamp `server` + `capability` onto `tool.metadata` (decision 4 · Layer 2)."""
    capability = _load_capability_map().get(server, {}).get(tool_name, "read")
    metadata = dict(tool.metadata or {})
    metadata.setdefault("server", server)
    metadata.setdefault("capability", capability)
    metadata.setdefault("full_name", f"{server}.{tool_name}")
    tool.metadata = metadata
    return tool


async def load_tools(
    client: MultiServerMCPClient | None = None,
) -> list[BaseTool]:
    """Discover all MCP tools, annotated with server + capability."""
    client = client or build_client()
    tools = await client.get_tools()
    annotated: list[BaseTool] = []
    for tool in tools:
        # tool_name_prefix=True ⇒ tool.name == f"{server}_{toolname}"
        name = tool.name
        server, _, raw = name.partition("_")
        annotated.append(_annotate(tool, server=server, tool_name=raw or name))
    return annotated
