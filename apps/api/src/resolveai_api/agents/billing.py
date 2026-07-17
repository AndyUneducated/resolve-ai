"""Billing Agent — delegates to the Plan-Execute-Replan sub-graph.

工具白名单：Stripe.* + Zendesk.get_ticket_history / update_ticket
升级条件：单笔 >= $500 / 怀疑 fraud
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage

from resolveai_api.agents.base import AgentConfig, BaseAgent
from resolveai_api.agents.billing_graph import build_billing_react, build_billing_subgraph
from resolveai_api.agents.state import GraphState

SYSTEM_PROMPT = """\
You are the Billing Agent. You handle refunds, subscription changes, invoices,
and charge disputes.

Process (Plan-and-Execute, NOT ReAct):
1. Generate a multi-step plan up-front (e.g. fetch charges → verify → refund → update ticket).
2. Execute the plan in batch via tool calls; do not improvise extra steps.
3. If single charge >= $500 OR fraud suspected → handoff to escalation with full context.

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
    """Compiles a per-instance billing sub-graph from filtered tools.

    `strategy` selects the reasoning style (variant D = plan_execute, variant C =
    react); `handoff` selects the payload shape (variant D = structured, variant
    B = full_transcript).
    """

    def __init__(
        self,
        *,
        handoff: str = "structured",
        strategy: str = "plan_execute",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        builder = build_billing_react if strategy == "react" else build_billing_subgraph
        self._subgraph = builder(
            tools=self.tools,
            whitelist=self.config.tool_whitelist,
            executor=self.executor,
            handoff=handoff,
        )

    @classmethod
    def default(
        cls,
        *,
        handoff: str = "structured",
        strategy: str = "plan_execute",
        **kwargs: Any,
    ) -> BillingAgent:
        from resolveai_api.config import get_settings

        settings = get_settings()
        config = AgentConfig(
            name="billing",
            model=settings.vertical_model,
            system_prompt=SYSTEM_PROMPT,
            tool_whitelist=list(TOOL_WHITELIST),
        )
        return cls(config=config, handoff=handoff, strategy=strategy, **kwargs)

    async def run(self, state: GraphState) -> GraphState:
        sub_input: dict[str, Any] = {
            "messages": list(state.get("messages", []) or []),
            "ticket_summary": dict(state.get("ticket_summary", {}) or {}),
            "plan": [],
            "past_steps": [],
            "iter_count": 0,
        }
        result = await self._subgraph.ainvoke(sub_input)

        response = result.get("response")
        past_steps = result.get("past_steps") or []
        escalate = bool(response.escalate) if response is not None else False
        if response is not None:
            assistant_text = response.final_answer
        elif past_steps:
            assistant_text = "I've worked on your billing issue:\n" + "\n".join(
                f"- {s}: {o}" for s, o in past_steps[-3:]
            )
        else:
            assistant_text = (
                "I couldn't make progress on this billing ticket within the iteration "
                "budget. Escalating."
            )

        tool_calls = list(state.get("tool_calls") or [])
        for step, observation in past_steps:
            tool_calls.append({"step": step, "observation": observation})

        # `escalate=True` makes the Supervisor route to the escalation node (a real
        # graph handoff), instead of the old advisory text suffix that ended the run.
        return {
            **state,
            "messages": [AIMessage(content=assistant_text)],
            "tool_calls": tool_calls,
            "escalate": escalate,
        }
