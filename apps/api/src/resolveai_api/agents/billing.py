"""Billing Agent — 退款 / 改订阅 / 发票 / 充值（Plan-and-Execute）。

工具白名单：Stripe.* + Zendesk.get_ticket_history / update_ticket
升级条件：单笔 > $500 / 怀疑 fraud
"""

from __future__ import annotations

from resolveai_api.agents.base import AgentConfig, BaseAgent
from resolveai_api.agents.state import GraphState

SYSTEM_PROMPT = """\
You are the Billing Agent. You handle refunds, subscription changes, invoices, and charge disputes.

Process (Plan-and-Execute, NOT ReAct):
1. Generate a multi-step plan up-front (e.g. fetch charges → verify → refund → update ticket).
2. Execute the plan in batch via tool calls; do not improvise extra steps.
3. If single charge > $500 OR fraud suspected → handoff to escalation with full context.

Hard rules:
- NEVER promise a refund amount that exceeds the actual charge.
- NEVER issue a discount code that is not in the approved list.
- ALL monetary values must be cross-checked against tool return values.
"""

TOOL_WHITELIST = [
    "stripe.list_charges",
    "stripe.get_charge",
    "stripe.refund",
    "zendesk.get_ticket_history",
    "zendesk.update_ticket",
]


class BillingAgent(BaseAgent):
    @classmethod
    def default(cls, **kwargs: object) -> BillingAgent:
        from resolveai_api.config import get_settings

        settings = get_settings()
        config = AgentConfig(
            name="billing",
            model=settings.vertical_model,
            system_prompt=SYSTEM_PROMPT,
            tool_whitelist=TOOL_WHITELIST,
        )
        return cls(config=config, **kwargs)  # type: ignore[arg-type]

    async def run(self, state: GraphState) -> GraphState:
        """TODO: planner.plan() → executor.run_plan()，每步走 sandboxed tool call。"""
        return {
            **state,
            "messages": [
                {
                    "role": "assistant",
                    "content": "[Billing Agent stub] 这里会跑 Plan-and-Execute → Stripe / Zendesk MCP。",
                }
            ],
        }
