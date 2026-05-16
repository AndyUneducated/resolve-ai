"""所有 Agent 的基类 — 强制实现"四件套"接入点。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from resolveai_api.agents.state import AgentName, GraphState
from resolveai_api.core.executor import Executor
from resolveai_api.core.memory import Memory
from resolveai_api.core.planner import Planner
from resolveai_api.core.tool import ToolBelt


@dataclass
class AgentConfig:
    name: AgentName
    model: str
    system_prompt: str
    tool_whitelist: list[str]
    """决策 4 · Layer 2 — capability whitelist；每个 Agent 只看自己的子集。"""


class BaseAgent(ABC):
    """Planner / Memory / Tool / Executor 四件套抽象的实现宿主。"""

    def __init__(
        self,
        config: AgentConfig,
        planner: Planner,
        memory: Memory,
        toolbelt: ToolBelt,
        executor: Executor,
    ) -> None:
        self.config = config
        self.planner = planner
        self.memory = memory
        self.toolbelt = toolbelt
        self.executor = executor

    @abstractmethod
    async def run(self, state: GraphState) -> GraphState:
        """LangGraph node 入口。"""
        raise NotImplementedError
