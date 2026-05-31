"""Architecture variants for the M7 ablation.

Each `VariantSpec` toggles four independent axes; `build_variant()` returns a
uniform `VariantRunner` so the harness treats every configuration identically.

| Key | topology | handoff         | strategy      | triage_tier |
|-----|----------|-----------------|---------------|-------------|
| A   | single   | -               | react         | vertical    |
| B   | multi    | full_transcript | plan_execute  | triage      |
| C   | multi    | structured      | react         | triage      |
| D   | multi    | structured      | plan_execute  | triage      | (baseline)

Runs invoke the compiled LangGraph DIRECTLY (no guardrail wrapper), so the
measured tokens/cost reflect the *architecture* rather than the (constant,
M5-owned) guardrail layer — in particular the vertical-tier policy judge.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, StateGraph

from resolveai_api.agents.state import GraphState
from resolveai_api.agents.supervisor import GraphOptions, SupervisorGraph
from resolveai_api.core.executor import Executor
from resolveai_api.core.llm import make_llm
from resolveai_api.guardrails.memory_isolator import MemoryIsolator
from resolveai_api.mcp.toolbelt import ToolBelt

MAX_SINGLE_AGENT_STEPS = 8


@dataclass(frozen=True)
class VariantSpec:
    key: str
    label: str
    topology: Literal["single", "multi"]
    handoff: Literal["structured", "full_transcript"]
    business_strategy: Literal["plan_execute", "react"]
    triage_tier: Literal["triage", "vertical"]

    def graph_options(self) -> GraphOptions:
        return GraphOptions(
            handoff=self.handoff,
            business_strategy=self.business_strategy,
            triage_tier=self.triage_tier,
        )


VARIANTS: dict[str, VariantSpec] = {
    "A": VariantSpec(
        key="A",
        label="Single-Agent + full toolbelt",
        topology="single",
        handoff="structured",
        business_strategy="react",
        triage_tier="vertical",
    ),
    "B": VariantSpec(
        key="B",
        label="4-Agent + full-transcript handoff",
        topology="multi",
        handoff="full_transcript",
        business_strategy="plan_execute",
        triage_tier="triage",
    ),
    "C": VariantSpec(
        key="C",
        label="4-Agent + ReAct",
        topology="multi",
        handoff="structured",
        business_strategy="react",
        triage_tier="triage",
    ),
    "D": VariantSpec(
        key="D",
        label="Final (structured + plan-execute + cost routing)",
        topology="multi",
        handoff="structured",
        business_strategy="plan_execute",
        triage_tier="triage",
    ),
    # Cost-routing ablation: identical to D but triage runs on the expensive
    # vertical tier (Sonnet-priced) instead of the cheap triage tier (Haiku).
    "D_triage_vertical": VariantSpec(
        key="D_triage_vertical",
        label="Final, triage on vertical tier (cost-routing OFF)",
        topology="multi",
        handoff="structured",
        business_strategy="plan_execute",
        triage_tier="vertical",
    ),
}

ABLATION_KEYS = ["A", "B", "C", "D"]
COST_ROUTING_KEYS = ["D", "D_triage_vertical"]


@dataclass
class RunResult:
    final_answer: str
    agent_path: list[str]
    tool_calls: list[dict] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    blocked: bool = False


def _final_answer(messages: list[BaseMessage]) -> str:
    for msg in reversed(messages or []):
        if isinstance(msg, AIMessage):
            content = msg.content
            return content if isinstance(content, str) else str(content)
    return ""


@dataclass
class VariantRunner:
    """Uniform async entry point wrapping a compiled LangGraph."""

    spec: VariantSpec
    graph: object
    run_fn: Callable[..., Awaitable[RunResult]]

    async def run(
        self, *, message: str, customer_id: str, tenant_id: str, thread_id: str
    ) -> RunResult:
        return await self.run_fn(
            message=message,
            customer_id=customer_id,
            tenant_id=tenant_id,
            thread_id=thread_id,
        )


SINGLE_AGENT_SYSTEM = """\
You are a single, full-stack enterprise customer-support agent. You handle
billing, technical, and escalation issues yourself using ALL available SaaS
tools (Stripe, Zendesk, Slack, Salesforce, Intercom).

Process (ReAct): inspect the request, call any tools you need to gather facts or
take action, observe the results, then reply with a final answer for the
customer.

Hard rules:
- ALL monetary values MUST be cross-checked against tool return values; never
  fabricate a charge_id or refund amount, and never refund beyond the charge.
- NEVER issue a discount/concession that a tool did not authorize.
- If a single charge >= $500 OR fraud is suspected, escalate via Slack + Zendesk
  instead of resolving it yourself.
When finished, reply with the final customer-facing answer and no tool calls.
"""


def _build_single_agent_graph(
    *,
    toolbelt: ToolBelt,
    executor: Executor,
    checkpointer: BaseCheckpointSaver,
):
    tools = toolbelt.tools
    whitelist = [
        (t.metadata or {}).get("full_name", t.name) for t in tools
    ]
    tool_index = {t.name: t for t in tools}
    llm = make_llm("vertical").bind_tools(tools) if tools else make_llm("vertical")

    async def agent_node(state: GraphState) -> GraphState:
        convo: list[BaseMessage] = [SystemMessage(content=SINGLE_AGENT_SYSTEM)]
        convo.extend(state.get("messages") or [])
        tool_calls_log = list(state.get("tool_calls") or [])
        for _ in range(MAX_SINGLE_AGENT_STEPS):
            ai = await llm.ainvoke(convo)
            if not isinstance(ai, AIMessage):
                ai = AIMessage(content=str(ai))
            tool_calls = getattr(ai, "tool_calls", []) or []
            convo.append(ai)
            if not tool_calls:
                return {**state, "messages": [ai], "tool_calls": tool_calls_log}
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
                    except Exception as exc:
                        obs = f"error: {exc}"
                tool_calls_log.append({"step": tname, "observation": obs[:1000]})
                convo.append(
                    ToolMessage(content=obs, tool_call_id=tool_call.get("id", tname))
                )
        return {
            **state,
            "messages": [
                AIMessage(
                    content="I've gathered what I can but hit the step budget; "
                    "escalating to a human agent."
                )
            ],
            "tool_calls": tool_calls_log,
        }

    builder: StateGraph = StateGraph(GraphState)
    builder.add_node("agent", agent_node)
    builder.set_entry_point("agent")
    builder.add_edge("agent", END)
    return builder.compile(checkpointer=checkpointer)


def _make_run_fn(graph: object, single_node: str | None):
    async def run_fn(
        *, message: str, customer_id: str, tenant_id: str, thread_id: str
    ) -> RunResult:
        namespace = MemoryIsolator.namespace(tenant_id, customer_id, thread_id or "default")
        config = {
            "configurable": {
                "thread_id": namespace,
                "user_tenant_id": tenant_id,
                "user_customer_id": customer_id,
            }
        }
        initial: GraphState = {
            "messages": [HumanMessage(content=message)],
            "tenant_id": tenant_id,
            "customer_id": customer_id,
            "thread_id": thread_id or "default",
            "tool_calls": [],
            "guardrail_flags": [],
        }
        agent_path: list[str] = []
        async for event in graph.astream(initial, config=config):  # type: ignore[attr-defined]
            for node_name in event:
                agent_path.append(node_name)
        snapshot = await graph.aget_state(config)  # type: ignore[attr-defined]
        values = snapshot.values if snapshot else {}
        messages = values.get("messages", []) if isinstance(values, dict) else []
        tool_calls = values.get("tool_calls", []) if isinstance(values, dict) else []
        flags = values.get("guardrail_flags", []) if isinstance(values, dict) else []
        if single_node is not None:
            agent_path = [single_node]
        return RunResult(
            final_answer=_final_answer(messages),
            agent_path=agent_path,
            tool_calls=list(tool_calls),
            flags=list(flags),
            blocked=False,
        )

    return run_fn


def build_variant(
    spec: VariantSpec,
    *,
    checkpointer: BaseCheckpointSaver,
    toolbelt: ToolBelt,
    executor: Executor | None = None,
) -> VariantRunner:
    executor = executor or Executor()
    if spec.topology == "single":
        graph = _build_single_agent_graph(
            toolbelt=toolbelt, executor=executor, checkpointer=checkpointer
        )
        return VariantRunner(spec=spec, graph=graph, run_fn=_make_run_fn(graph, "agent"))

    supervisor = SupervisorGraph(
        checkpointer=checkpointer,
        toolbelt=toolbelt,
        options=spec.graph_options(),
        executor=executor,
    )
    return VariantRunner(
        spec=spec, graph=supervisor.graph, run_fn=_make_run_fn(supervisor.graph, None)
    )
