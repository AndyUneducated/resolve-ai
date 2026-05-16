"""Technical Agent — bug 报告 / 功能问询 / 配置帮助。

工具白名单：Zendesk + GitHub Issues + 内部 KB 检索
升级条件：无法复现 bug / 涉及 SLA
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage

from resolveai_api.agents.base import AgentConfig, BaseAgent
from resolveai_api.agents.state import GraphState

SYSTEM_PROMPT = """\
You are the Technical Agent. You handle bug reports, feature questions, and configuration help.

Process (Plan-and-Execute):
1. Search internal KB / FAQ via hybrid retrieval first.
2. If reproducible from logs / past tickets, propose a fix.
3. If not reproducible OR SLA-impacting → handoff to escalation.

Hard rules:
- NEVER promise features that are not in the product changelog.
- ALWAYS cite KB doc IDs when giving instructions.
"""

TOOL_WHITELIST = [
    "zendesk.get_ticket_history",
    "zendesk.update_ticket",
    "kb.search",
]


class TechnicalAgent(BaseAgent):
    @classmethod
    def default(cls, **kwargs: Any) -> TechnicalAgent:
        from resolveai_api.config import get_settings

        settings = get_settings()
        config = AgentConfig(
            name="technical",
            model=settings.vertical_model,
            system_prompt=SYSTEM_PROMPT,
            tool_whitelist=list(TOOL_WHITELIST),
        )
        return cls(config=config, **kwargs)

    async def run(self, state: GraphState) -> GraphState:
        """TODO (M6): hybrid retrieval + plan-and-execute via billing_graph."""
        return {
            **state,
            "messages": [
                AIMessage(
                    content="[Technical Agent stub] KB lookup + multi-step plan lands in M6."
                )
            ],
        }
