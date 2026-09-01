"""Base class for all agents — holds config + filtered MCP tools + Executor.

Each Agent receives a **pre-filtered** `list[BaseTool]` (sliced to its
`TOOL_WHITELIST` via `mcp/toolbelt.py:ToolBelt.for_agent`); enforcing the
whitelist again happens at call-time in [`core/executor.py`](../core/executor.py).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from langchain_core.tools import BaseTool

from resolveai_api.agents.state import AgentName, GraphState
from resolveai_api.core.executor import Executor


def find_tool(tools: list[BaseTool], full_name: str) -> BaseTool | None:
    """Return the tool whose dot-form `metadata.full_name` matches, else None.

    Shared by the agents that reach for a specific MCP tool by name (technical /
    escalation), so the lookup lives in one place.
    """
    for tool in tools:
        if (tool.metadata or {}).get("full_name") == full_name:
            return tool
    return None


@dataclass
class AgentConfig:
    name: AgentName
    model: str
    """Model identifier consumed by the LLM factory (e.g. 'qwen3.5:9b')."""
    system_prompt: str
    tool_whitelist: list[str] = field(default_factory=list)
    """Decision 4 · Layer 2 — capability whitelist; each agent sees only its subset."""


class BaseAgent(ABC):
    """Common surface for the four customer-support agents."""

    def __init__(
        self,
        *,
        config: AgentConfig,
        tools: list[BaseTool] | None = None,
        executor: Executor | None = None,
    ) -> None:
        self.config = config
        self.tools: list[BaseTool] = tools or []
        self.executor: Executor = executor or Executor()

    @abstractmethod
    async def run(self, state: GraphState) -> GraphState:
        """LangGraph node entry point."""
        raise NotImplementedError
