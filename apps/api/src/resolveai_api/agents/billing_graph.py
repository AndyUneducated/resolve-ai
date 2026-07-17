"""Billing sub-graph — Plan-Execute-Replan loop.

行业对齐：完全照 LangGraph 官方 blog *Planning Agents (2024)* 的三节点闭环：

    planner ─► executor ─► replanner ─► (executor | END)

State：在 `GraphState` 之外加 `plan: list[str]` / `past_steps: list[(step, observation)]`
和 `response: str`。`response` 非空即终止。`MAX_STEPS` 防止 LLM 死循环。

工具集 = 已经按 Agent capability whitelist 过滤过的 LangChain `BaseTool`。
工具调用通过 [`core/executor.py`](../core/executor.py) 的 capability gate。
"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any, Literal, TypedDict, cast

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import BaseTool
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

from resolveai_api.core.budget import over_cost_budget
from resolveai_api.core.executor import Executor
from resolveai_api.core.llm import make_llm, make_structured_llm

logger = logging.getLogger(__name__)

MAX_STEPS = 6
"""Hard cap on (executor + replanner) iterations to bound runaway LLM loops."""

Handoff = str
"""`structured` (compact TicketSummary JSON) | `full_transcript` (raw messages)."""


class Plan(BaseModel):
    """Multi-step plan produced by the planner / replanner."""

    steps: list[str] = Field(
        default_factory=list,
        description="Ordered, atomic steps. Each step is a natural-language goal.",
    )


class Response(BaseModel):
    """Terminal answer for the customer."""

    final_answer: str = Field(..., description="What to say to the customer.")
    escalate: bool = Field(
        default=False,
        description="True iff this ticket must be handed off to a human agent.",
    )
    reason: str | None = Field(default=None)


class Replan(BaseModel):
    """Replanner output: either continue with a new plan, or finalize."""

    plan: Plan | None = None
    response: Response | None = None


class BillingState(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    ticket_summary: dict[str, Any]
    plan: list[str]
    past_steps: list[tuple[str, str]]
    response: Response | None
    iter_count: int


PLANNER_SYSTEM = """\
You are the planner for a billing customer-support agent.

Goal: produce an ordered list of atomic steps that, executed in sequence by a
tool-using executor, will resolve the customer's billing issue.

Hard rules:
- Every step MUST be expressible in 1 sentence.
- Steps that read state (list charges, fetch ticket history) come before steps that mutate (refund, update ticket).
- If single charge >= $500 OR fraud suspected, the LAST step must be "escalate to a human".
- Never propose a step that promises refunds beyond the actual charge amount.
"""

EXECUTOR_SYSTEM = """\
You are the executor for a billing agent. Carry out exactly ONE step using the
available tools. If the step is already satisfied by past observations, briefly
say so and do not call a tool.

Hard rules:
- ALL monetary values must be cross-checked against tool return values.
- NEVER fabricate charge_id / refund amount.
"""

REPLANNER_SYSTEM = """\
You are the replanner. Given the original ticket, the past_steps observations,
and the remaining plan, decide:

- If the customer's issue is resolved OR must escalate, emit a `Response`.
- Otherwise emit an updated `Plan` (drop completed steps; add steps if needed).

Hard rules:
- Set `escalate=true` if any charge >= $500 needs refunding, fraud suspected,
  or you cannot proceed safely.
- Never invent monetary amounts; only cite values that appeared in past_steps.
"""


def _msg_text(msg: BaseMessage | dict | object) -> str:
    content = getattr(msg, "content", None)
    if content is None and isinstance(msg, dict):
        content = msg.get("content")
    return content if isinstance(content, str) else str(content or "")


def _ticket_brief(state: BillingState, handoff: Handoff = "structured") -> str:
    """Handoff payload for the billing LLM.

    `structured` (variant D) passes the compact TicketSummary; `full_transcript`
    (variant B) replays every message so we can measure the token cost of NOT
    summarizing at handoff time.
    """
    if handoff == "full_transcript":
        msgs = state.get("messages") or []
        transcript = "\n".join(_msg_text(m) for m in msgs if _msg_text(m))
        return transcript or json.dumps(state.get("ticket_summary") or {}, default=str)
    summary = state.get("ticket_summary") or {}
    return json.dumps(summary, default=str)


def _format_past_steps(past: list[tuple[str, str]]) -> str:
    if not past:
        return "(no steps executed yet)"
    return "\n".join(f"{i + 1}. {s} → {o}" for i, (s, o) in enumerate(past))


def _build_planner_node(handoff: Handoff = "structured"):
    async def planner(state: BillingState) -> BillingState:
        ticket = _ticket_brief(state, handoff)
        llm = make_structured_llm("vertical", Plan)
        plan: Plan = cast(
            Plan,
            await llm.ainvoke(
                [
                    SystemMessage(content=PLANNER_SYSTEM),
                    HumanMessage(
                        content=f"Ticket summary (JSON):\n{ticket}\n\nProduce the plan."
                    ),
                ]
            ),
        )
        return {
            **state,
            "plan": list(plan.steps),
            "past_steps": list(state.get("past_steps") or []),
            "iter_count": int(state.get("iter_count") or 0),
        }

    return planner


def _build_executor_node(tools: list[BaseTool], executor: Executor, whitelist: list[str]):
    tool_index = {t.name: t for t in tools}
    llm = make_llm("vertical").bind_tools(tools) if tools else make_llm("vertical")

    async def executor_node(state: BillingState) -> BillingState:
        plan = list(state.get("plan") or [])
        past_steps = list(state.get("past_steps") or [])
        iter_count = int(state.get("iter_count") or 0) + 1

        if not plan:
            return {**state, "iter_count": iter_count}

        current_step = plan[0]
        prompt = (
            f"Step to execute: {current_step}\n\n"
            f"Past observations:\n{_format_past_steps(past_steps)}"
        )

        ai_message = await llm.ainvoke(
            [
                SystemMessage(content=EXECUTOR_SYSTEM),
                HumanMessage(content=prompt),
            ]
        )

        observation_parts: list[str] = []
        if isinstance(ai_message.content, str) and ai_message.content.strip():
            observation_parts.append(ai_message.content.strip())

        for tool_call in getattr(ai_message, "tool_calls", []) or []:
            tname = tool_call["name"] if isinstance(tool_call, dict) else tool_call.name
            targs = (
                tool_call["args"] if isinstance(tool_call, dict) else tool_call.args
            ) or {}
            tool = tool_index.get(tname)
            if tool is None:
                observation_parts.append(f"unknown_tool:{tname}")
                continue
            try:
                result = await executor.call_tool(
                    tool=tool, args=targs, whitelist=whitelist
                )
                observation_parts.append(
                    f"{tname}({json.dumps(targs, default=str)}) -> {result.output!s}"
                )
            except PermissionError as exc:
                observation_parts.append(f"{tname} blocked by capability whitelist: {exc}")

        observation = "\n".join(observation_parts) or "(no-op)"
        past_steps.append((current_step, observation))
        return {
            **state,
            "plan": plan[1:],
            "past_steps": past_steps,
            "iter_count": iter_count,
        }

    return executor_node


def _build_replanner_node(handoff: Handoff = "structured"):
    async def replanner(state: BillingState) -> BillingState:
        ticket = _ticket_brief(state, handoff)
        past = _format_past_steps(state.get("past_steps") or [])
        remaining = state.get("plan") or []
        llm = make_structured_llm("vertical", Replan)
        result: Replan = cast(
            Replan,
            await llm.ainvoke(
                [
                    SystemMessage(content=REPLANNER_SYSTEM),
                    HumanMessage(
                        content=(
                            f"Ticket summary (JSON):\n{ticket}\n\n"
                            f"Past steps:\n{past}\n\n"
                            f"Remaining plan:\n{remaining}\n\n"
                            "Decide: continue with an updated plan or finalize with a response."
                        )
                    ),
                ]
            ),
        )

        update: BillingState = {**state}
        if result.response is not None:
            update["response"] = result.response
        elif result.plan is not None:
            update["plan"] = list(result.plan.steps)
        return update

    return replanner


Decision = Literal["execute", "replan", "done"]


def _route_after_executor(state: BillingState) -> Decision:
    # Cost circuit-breaker (M11): stop spending once the run blows its budget.
    # Finalize with observations gathered so far rather than firing more LLM hops.
    if over_cost_budget():
        return "done"
    if (state.get("iter_count") or 0) >= MAX_STEPS:
        return "done"
    if state.get("response") is not None:
        return "done"
    return "execute" if (state.get("plan") or []) else "replan"


def _route_after_replanner(state: BillingState) -> Decision:
    if state.get("response") is not None:
        return "done"
    if over_cost_budget():  # cost circuit-breaker (M11)
        return "done"
    if (state.get("iter_count") or 0) >= MAX_STEPS:
        return "done"
    return "execute" if (state.get("plan") or []) else "done"


REACT_SYSTEM = """\
You are a billing customer-support agent operating in a single-step ReAct loop
(NO up-front plan): look at the ticket, optionally call ONE or more tools, observe
results, then either call more tools or give a final answer.

Hard rules:
- ALL monetary values must be cross-checked against tool return values.
- NEVER fabricate charge_id / refund amount; never promise a refund beyond the charge.
- If a single charge >= $500 OR fraud is suspected, stop and say you are escalating.
When you are done, reply with the final customer-facing answer and no tool calls.
"""


def build_billing_react(
    *,
    tools: list[BaseTool],
    whitelist: list[str],
    executor: Executor | None = None,
    handoff: Handoff = "structured",
):
    """Compile a single-node ReAct billing agent (variant C ablation).

    Same tools + capability gate as the Plan-Execute graph, but the model
    improvises one step at a time instead of committing to a multi-step plan.
    Emits the same `response` / `past_steps` keys so `BillingAgent.run` is
    strategy-agnostic.
    """
    executor = executor or Executor()
    tool_index = {t.name: t for t in tools}
    llm = make_llm("vertical").bind_tools(tools) if tools else make_llm("vertical")

    async def agent_node(state: BillingState) -> BillingState:
        brief = _ticket_brief(state, handoff)
        convo: list[BaseMessage] = [
            SystemMessage(content=REACT_SYSTEM),
            HumanMessage(content=f"Ticket:\n{brief}\n\nResolve it."),
        ]
        past_steps: list[tuple[str, str]] = []
        for _ in range(MAX_STEPS):
            if over_cost_budget():  # cost circuit-breaker (M11)
                break
            ai = await llm.ainvoke(convo)
            if not isinstance(ai, AIMessage):
                ai = AIMessage(content=str(ai))
            tool_calls = getattr(ai, "tool_calls", []) or []
            if not tool_calls:
                final = ai.content if isinstance(ai.content, str) else str(ai.content)
                escalate = "escalat" in final.lower()
                return {
                    **state,
                    "response": Response(
                        final_answer=final.strip() or "Resolved.", escalate=escalate
                    ),
                    "past_steps": past_steps,
                }
            convo.append(ai)
            for tool_call in tool_calls:
                tname = tool_call["name"]
                targs = tool_call.get("args") or {}
                tool = tool_index.get(tname)
                if tool is None:
                    obs = f"unknown_tool:{tname}"
                else:
                    try:
                        result = await executor.call_tool(
                            tool=tool, args=targs, whitelist=whitelist
                        )
                        obs = str(result.output)
                    except Exception as exc:  # capability gate / tool error
                        obs = f"error: {exc}"
                past_steps.append((tname, obs))
                convo.append(
                    ToolMessage(content=obs, tool_call_id=tool_call.get("id", tname))
                )
        return {
            **state,
            "response": Response(
                final_answer="Reached the step budget without resolving; escalating.",
                escalate=True,
            ),
            "past_steps": past_steps,
        }

    builder: StateGraph = StateGraph(BillingState)
    builder.add_node("agent", agent_node)
    builder.set_entry_point("agent")
    builder.add_edge("agent", END)
    return builder.compile()


def build_billing_subgraph(
    *,
    tools: list[BaseTool],
    whitelist: list[str],
    executor: Executor | None = None,
    handoff: Handoff = "structured",
):
    """Compile the planner/executor/replanner sub-graph (no checkpointer here).

    The parent SupervisorGraph attaches its checkpointer at compile time and
    propagates state to / from this sub-graph implicitly via shared keys.
    """
    executor = executor or Executor()

    builder: StateGraph = StateGraph(BillingState)
    builder.add_node("planner", _build_planner_node(handoff))
    builder.add_node("executor", _build_executor_node(tools, executor, whitelist))
    builder.add_node("replanner", _build_replanner_node(handoff))

    builder.set_entry_point("planner")
    builder.add_edge("planner", "executor")
    builder.add_conditional_edges(
        "executor",
        _route_after_executor,
        {"execute": "executor", "replan": "replanner", "done": END},
    )
    builder.add_conditional_edges(
        "replanner",
        _route_after_replanner,
        {"execute": "executor", "done": END},
    )
    return builder.compile()
