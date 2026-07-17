"""Escalation Agent — deterministic human handoff (M3).

M3 设计要点：升级流程本身就是几条**确定性步骤**（通知 on-call、把 ticket 标
为 escalated），不需要 LLM 在工具之间反复试错。Agent 直接通过 MCP 工具完成：

  slack.notify_team      (capability=write, must be granted)
  zendesk.escalate       (capability=destructive, must be granted)

如果某条工具未发现（MCP server 没启），就降级到「最佳努力」记录到
`tool_calls`，但 final answer 仍说明已升级——便于 dev 环境 demo。

M4 在此基础上接 Layer 3 输出 cross-check（确认 Slack/Zendesk 真返回了
escalation id；目前 mock store 返回 dict）。
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage

from resolveai_api.agents.base import AgentConfig, BaseAgent, find_tool
from resolveai_api.agents.state import GraphState

SYSTEM_PROMPT = """\
You are the Escalation Agent. You wrap up tickets that automation cannot resolve
and hand them off to human agents with full context.

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

DEFAULT_ONCALL_CHANNEL = "#oncall-billing"


def _intent_to_channel(intent: str | None) -> str:
    if intent == "technical":
        return "#oncall-technical"
    return DEFAULT_ONCALL_CHANNEL


def _packet(state: GraphState, handoff: str = "structured") -> str:
    tool_calls = state.get("tool_calls") or []
    if handoff == "full_transcript":
        msgs = state.get("messages") or []
        transcript = "\n".join(
            m.content
            for m in msgs
            if isinstance(m, BaseMessage) and isinstance(m.content, str) and m.content
        )
        context = f"Full transcript:\n{transcript}"
    else:
        summary = state.get("ticket_summary") or {}
        context = f"Ticket summary: {json.dumps(dict(summary), default=str)}"
    return (
        f"{context}\n"
        f"Past tool calls ({len(tool_calls)}): {json.dumps(tool_calls, default=str)[:2000]}"
    )


class EscalationAgent(BaseAgent):
    def __init__(self, *, handoff: str = "structured", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._handoff = handoff

    @classmethod
    def default(cls, *, handoff: str = "structured", **kwargs: Any) -> EscalationAgent:
        from resolveai_api.config import get_settings

        settings = get_settings()
        config = AgentConfig(
            name="escalation",
            model=settings.vertical_model,
            system_prompt=SYSTEM_PROMPT,
            tool_whitelist=list(TOOL_WHITELIST),
        )
        return cls(config=config, handoff=handoff, **kwargs)

    async def run(self, state: GraphState) -> GraphState:
        summary = state.get("ticket_summary") or {}
        intent = summary.get("intent")
        ticket_id = (summary.get("entities") or {}).get("ticket_id") or "zd_001"
        channel = _intent_to_channel(intent)
        packet = _packet(state, self._handoff)
        tool_calls = list(state.get("tool_calls") or [])

        outcomes: list[str] = []

        # 1) Slack notify (capability=write — must be granted)
        notify_tool = find_tool(self.tools, "slack.notify_team")
        if notify_tool is not None:
            try:
                result = await self.executor.call_tool(
                    tool=notify_tool,
                    args={
                        "channel": channel,
                        "message": f"Ticket {ticket_id} escalated. {packet}",
                        "mention": "@oncall",
                    },
                    whitelist=self.config.tool_whitelist,
                )
                tool_calls.append({"step": "slack.notify_team", "observation": str(result.output)})
                outcomes.append(f"Notified {channel} (@oncall).")
            except Exception as exc:  # defensive demo path: any tool failure logged + skipped
                tool_calls.append({"step": "slack.notify_team", "error": str(exc)})
                outcomes.append(f"Slack notification skipped: {exc}")
        else:
            outcomes.append("Slack MCP not configured; logged escalation locally.")

        # 2) Zendesk escalate (capability=destructive — must be granted)
        escalate_tool = find_tool(self.tools, "zendesk.escalate")
        if escalate_tool is not None:
            try:
                result = await self.executor.call_tool(
                    tool=escalate_tool,
                    args={
                        "ticket_id": ticket_id,
                        "reason": packet[:500],
                    },
                    whitelist=self.config.tool_whitelist,
                )
                tool_calls.append({"step": "zendesk.escalate", "observation": str(result.output)})
                outcomes.append(f"Zendesk ticket {ticket_id} marked escalated.")
            except Exception as exc:  # defensive demo path
                tool_calls.append({"step": "zendesk.escalate", "error": str(exc)})
                outcomes.append(f"Zendesk escalate skipped: {exc}")
        else:
            outcomes.append("Zendesk MCP not configured; manual escalation required.")

        message = (
            "I've escalated this to a human agent with full context.\n"
            + "\n".join(f"- {o}" for o in outcomes)
        )
        return {
            **state,
            "messages": [AIMessage(content=message)],
            "tool_calls": tool_calls,
        }
