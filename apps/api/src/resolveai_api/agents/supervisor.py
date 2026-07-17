"""Supervisor — LangGraph orchestration: Triage → vertical agent → END.

Compile-time wiring:
- `checkpointer` (AsyncPostgresSaver | MemorySaver) for stateful handoff & resume.
- `toolbelt` (M3) or legacy `mcp_tools` list: discovered MCP tools, sliced per-agent.

Per-request:
- `thread_id = "{tenant}::{customer}::{thread}"` is namespaced for cross-tenant
  isolation (decision 4 · Layer 4).
"""

from __future__ import annotations

import contextlib
import json
import time
from collections.abc import AsyncIterator, Callable, MutableSequence
from dataclasses import dataclass
from typing import Literal

from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from resolveai_api.agents.billing import TOOL_WHITELIST as BILLING_WHITELIST
from resolveai_api.agents.billing import BillingAgent
from resolveai_api.agents.escalation import TOOL_WHITELIST as ESCALATION_WHITELIST
from resolveai_api.agents.escalation import EscalationAgent
from resolveai_api.agents.state import GraphState
from resolveai_api.agents.technical import TOOL_WHITELIST as TECHNICAL_WHITELIST
from resolveai_api.agents.technical import TechnicalAgent
from resolveai_api.agents.triage import TriageAgent
from resolveai_api.config import get_settings
from resolveai_api.core.checkpointer import CrossTenantAccessBlockedError
from resolveai_api.core.executor import Executor
from resolveai_api.core.usage import RunTrace, capture_run
from resolveai_api.guardrails.attribution import (
    BlockKind,
    GuardrailReport,
    block_kind,
    resolve_fail_closed,
)
from resolveai_api.guardrails.input_filter import InputGuardrail
from resolveai_api.guardrails.memory_isolator import MemoryIsolator
from resolveai_api.guardrails.output_filter import OutputGuardrail
from resolveai_api.mcp.toolbelt import ToolBelt
from resolveai_api.observability.tracing import get_tracer, span

_TRACER = get_tracer("resolveai.supervisor")


@dataclass(frozen=True)
class GraphOptions:
    """Architecture knobs for the multi-agent graph (M7 ablation).

    Defaults are variant D (the production configuration), so existing callers
    that omit `options` keep the exact same behavior.
    """

    handoff: Literal["structured", "full_transcript"] = "structured"
    business_strategy: Literal["plan_execute", "react"] = "plan_execute"
    triage_tier: Literal["triage", "vertical"] = "triage"


def _build_agents(
    toolbelt: ToolBelt,
    options: GraphOptions,
    executor: Executor,
) -> dict[str, object]:
    return {
        "triage": TriageAgent.default(
            tools=[], executor=executor, tier=options.triage_tier
        ),
        "billing": BillingAgent.default(
            tools=toolbelt.for_agent(BILLING_WHITELIST),
            executor=executor,
            handoff=options.handoff,
            strategy=options.business_strategy,
        ),
        "technical": TechnicalAgent.default(
            tools=toolbelt.for_agent(TECHNICAL_WHITELIST),
            executor=executor,
            handoff=options.handoff,
        ),
        "escalation": EscalationAgent.default(
            tools=toolbelt.for_agent(ESCALATION_WHITELIST),
            executor=executor,
            handoff=options.handoff,
        ),
    }


def _route_after_triage(state: GraphState) -> Literal["billing", "technical", "escalation", END]:
    summary = state.get("ticket_summary", {})
    intent = summary.get("intent", "other")
    if intent in ("billing", "technical", "escalation"):
        return intent  # type: ignore[return-value]
    return END


def _route_after_vertical(state: GraphState) -> str:
    """Billing / Technical → escalation node when they request a human handoff.

    This is a real graph edge (the "4-agent handoff" story), replacing the old
    advisory text suffix. Defaults to END when `escalate` is unset/False, so the
    common path is byte-identical to before.
    """
    return "escalation" if state.get("escalate") else END


def _run_metrics(trace: RunTrace) -> dict[str, object]:
    """Per-request cost/usage snapshot streamed in the `done` event.

    Token counts are real (captured by the tier callbacks); `cost_usd` is modeled
    from representative per-tier prices (`eval.pricing`).
    """
    from resolveai_api.eval.pricing import trace_cost_usd

    return {
        "tokens": trace.total_tokens,
        "input_tokens": trace.total_input,
        "output_tokens": trace.total_output,
        "cost_usd": round(trace_cost_usd(trace), 6),
        "tool_calls": len(trace.tool_calls),
        "usage_by_tier": {
            tier: {
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "calls": usage.calls,
            }
            for tier, usage in trace.usage_by_tier.items()
        },
    }


def _extract_text(msg: BaseMessage | dict | object) -> str:
    if isinstance(msg, BaseMessage):
        content = msg.content
    elif isinstance(msg, dict):
        content = msg.get("content", "")
    else:
        content = ""
    if isinstance(content, str):
        return content
    return str(content)


def _set(otel_span: object | None, key: str, value: object) -> None:
    """Set an attribute on an optional OTel span (no-op when tracing is off)."""
    if otel_span is None:
        return
    with contextlib.suppress(Exception):  # pragma: no cover - defensive
        otel_span.set_attribute(key, value)  # type: ignore[attr-defined]


ReportSink = Callable[[GuardrailReport], None] | MutableSequence[GuardrailReport]


def _emit_report(report_sink: ReportSink | None, flags: list[str]) -> None:
    if report_sink is None:
        return
    report = GuardrailReport.from_flags(flags)
    if callable(report_sink):
        report_sink(report)
        return
    report_sink.append(report)


class SupervisorGraph:
    """LangGraph supervisor wired with checkpointer + a ToolBelt.

    `toolbelt` is the M3 surface; `mcp_tools` is kept for backwards compatibility
    so existing tests (and any caller that already filtered tools) keep working.
    """

    def __init__(
        self,
        *,
        checkpointer: BaseCheckpointSaver,
        toolbelt: ToolBelt | None = None,
        mcp_tools: list[BaseTool] | None = None,
        options: GraphOptions | None = None,
        executor: Executor | None = None,
    ) -> None:
        if toolbelt is None:
            toolbelt = ToolBelt(mcp_tools or [])
        self.checkpointer = checkpointer
        self.toolbelt = toolbelt
        self.options = options or GraphOptions()
        self.agents = _build_agents(toolbelt, self.options, executor or Executor())
        self.input_guard = InputGuardrail()
        self.output_guard = OutputGuardrail()
        self.graph = self._build_graph()

    def _build_graph(self):
        builder: StateGraph = StateGraph(GraphState)

        builder.add_node("triage", self.agents["triage"].run)  # type: ignore[attr-defined]
        builder.add_node("billing", self.agents["billing"].run)  # type: ignore[attr-defined]
        builder.add_node("technical", self.agents["technical"].run)  # type: ignore[attr-defined]
        builder.add_node("escalation", self.agents["escalation"].run)  # type: ignore[attr-defined]

        builder.add_edge(START, "triage")
        builder.add_conditional_edges(
            "triage",
            _route_after_triage,
            {
                "billing": "billing",
                "technical": "technical",
                "escalation": "escalation",
                END: END,
            },
        )
        # Billing / Technical can hand off to escalation (real edge, gated on the
        # `escalate` flag); otherwise they end the run.
        for vertical in ("billing", "technical"):
            builder.add_conditional_edges(
                vertical,
                _route_after_vertical,
                {"escalation": "escalation", END: END},
            )
        builder.add_edge("escalation", END)

        return builder.compile(checkpointer=self.checkpointer)

    async def stream(
        self,
        *,
        message: str,
        customer_id: str,
        tenant_id: str | None,
        thread_id: str | None,
        report_sink: ReportSink | None = None,
    ) -> AsyncIterator[dict[str, str]]:
        settings = get_settings()
        tenant_id = tenant_id or settings.default_tenant_id
        thread_id = thread_id or "default"
        # Namespace is built from (tenant_id, customer_id, thread_id); the real
        # cross-tenant defense lives in IsolatedCheckpointer (which compares the
        # *stored* checkpoint namespace against the current request identity).
        namespace = MemoryIsolator.namespace(tenant_id, customer_id, thread_id)
        # Fail-closed: a degraded guard (timeout / unavailable) blocks instead of
        # passing through. "auto" follows env_profile (production → closed).
        fail_closed = resolve_fail_closed(
            getattr(settings, "guardrail_fail_closed", "auto"),
            getattr(settings, "env_profile", "demo"),
        )
        guardrail_ms = {"input": 0.0, "output": 0.0}

        with capture_run() as run_trace, span(
            _TRACER,
            "ticket.run",
            attributes={
                "tenant_id": tenant_id,
                "customer_id": customer_id,
                "thread_id": thread_id,
            },
        ) as ticket_span:
            # Layer 1 input guardrails
            _t0 = time.perf_counter()
            scrubbed, flags = await self.input_guard.scan_and_redact(message)
            guardrail_ms["input"] = (time.perf_counter() - _t0) * 1000.0
            all_flags = list(flags)
            kind = block_kind(flags, fail_closed=fail_closed)
            if kind is not BlockKind.NONE:
                _emit_report(report_sink, all_flags)
                _set(ticket_span, "outcome", "blocked")
                _set(ticket_span, "blocked_layer", "input")
                _set(ticket_span, "blocked_kind", str(kind))
                with span(_TRACER, "guardrail.block", attributes={"layer": "input"}):
                    pass
                yield {
                    "type": "blocked",
                    "data": json.dumps(
                        {"reason": flags, "layer": "input", "kind": str(kind)}
                    ),
                }
                return

            initial: GraphState = {
                "messages": [HumanMessage(content=scrubbed)],
                "tenant_id": tenant_id,
                "customer_id": customer_id,
                "thread_id": thread_id,
                "tool_calls": [],
                "guardrail_flags": flags,
            }
            config = {
                "configurable": {
                    "thread_id": namespace,
                    "user_tenant_id": tenant_id,
                    "user_customer_id": customer_id,
                }
            }

            try:
                async for event in self.graph.astream(initial, config=config):
                    for node_name, node_state in event.items():
                        msgs = (
                            node_state.get("messages", [])
                            if isinstance(node_state, dict)
                            else []
                        )
                        if not msgs:
                            continue
                        content = _extract_text(msgs[-1])
                        # Layer 3 output guardrails
                        tool_calls = (
                            node_state.get("tool_calls", [])
                            if isinstance(node_state, dict)
                            else []
                        )
                        with span(
                            _TRACER,
                            f"agent.{node_name}",
                            attributes={
                                "agent": node_name,
                                "content_len": len(content),
                            },
                        ) as agent_span:
                            _t1 = time.perf_counter()
                            safe, out_flags = await self.output_guard.scan(
                                content, tool_calls
                            )
                            guardrail_ms["output"] += (
                                time.perf_counter() - _t1
                            ) * 1000.0
                            _set(agent_span, "flag_count", len(out_flags))
                            if out_flags:
                                all_flags.extend(out_flags)
                            out_kind = block_kind(out_flags, fail_closed=fail_closed)
                            if out_kind is not BlockKind.NONE:
                                _emit_report(report_sink, all_flags)
                                _set(agent_span, "blocked", True)
                                _set(ticket_span, "outcome", "blocked")
                                _set(ticket_span, "blocked_layer", "output")
                                _set(ticket_span, "blocked_kind", str(out_kind))
                                with span(
                                    _TRACER,
                                    "guardrail.block",
                                    attributes={"layer": "output"},
                                ):
                                    pass
                                yield {
                                    "type": "blocked",
                                    "data": json.dumps(
                                        {
                                            "reason": out_flags,
                                            "layer": "output",
                                            "kind": str(out_kind),
                                        }
                                    ),
                                }
                                return
                        yield {
                            "type": "agent_step",
                            "data": json.dumps(
                                {
                                    "agent": node_name,
                                    "content": safe,
                                    "flags": out_flags,
                                    "tool_calls": tool_calls,
                                },
                                default=str,
                            ),
                        }
            except CrossTenantAccessBlockedError:
                all_flags.append("cross_tenant_blocked")
                _emit_report(report_sink, all_flags)
                _set(ticket_span, "outcome", "blocked")
                _set(ticket_span, "blocked_layer", "tenant_isolation")
                with span(
                    _TRACER, "guardrail.block", attributes={"layer": "tenant_isolation"}
                ):
                    pass
                yield {
                    "type": "blocked",
                    "data": json.dumps({"reason": ["cross_tenant_blocked"]}),
                }
                return
            _emit_report(report_sink, all_flags)
            _set(ticket_span, "outcome", "done")
            _set(ticket_span, "flag_count", len(all_flags))
            metrics = _run_metrics(run_trace)
            metrics["guardrail_latency_ms"] = {
                layer: round(ms, 2) for layer, ms in guardrail_ms.items()
            }
            metrics["fail_closed"] = fail_closed
            _set(ticket_span, "total_tokens", metrics["tokens"])
            _set(ticket_span, "cost_usd", metrics["cost_usd"])
            yield {"type": "done", "data": json.dumps(metrics, default=str)}
