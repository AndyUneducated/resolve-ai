"""Escalation Agent — 收尾 + 转人工。

工具白名单：Slack notify + Zendesk handoff
本身就是升级终点。
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage

from resolveai_api.agents.base import AgentConfig, BaseAgent
from resolveai_api.agents.state import GraphState

SYSTEM_PROMPT = """\
You are the Escalation Agent. You wrap up tickets that automation cannot resolve and hand them off
to human agents with full context.

Process:
1. Summarize the conversation + every tool call so far in a structured handoff packet.
2. Notify the on-call team via Slack with @-mention based on intent.
3. Mark the Zendesk ticket as `escalated` with the handoff packet attached.

Hard rules:
- NEVER attempt to resolve the ticket yourself.
- ALWAYS include all `tool_calls` from state in the handoff for auditability.
"""

TOOL_WHITELIST = [
    "slack.notify_team",
    "zendesk.update_ticket",
    "zendesk.escalate",
]


class EscalationAgent(BaseAgent):
    @classmethod
    def default(cls, **kwargs: Any) -> EscalationAgent:
        from resolveai_api.config import get_settings

        settings = get_settings()
        config = AgentConfig(
            name="escalation",
            model=settings.vertical_model,
            system_prompt=SYSTEM_PROMPT,
            tool_whitelist=list(TOOL_WHITELIST),
        )
        return cls(config=config, **kwargs)

    async def run(self, state: GraphState) -> GraphState:
        """TODO (M3): wire Slack + Zendesk MCP servers and emit handoff packet."""
        return {
            **state,
            "messages": [
                AIMessage(
                    content="[Escalation Agent stub] human handoff lands in M3 once Slack/Zendesk MCP are real."
                )
            ],
        }
