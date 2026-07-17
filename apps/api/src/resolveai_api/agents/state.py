"""LangGraph 共享 state — Stateful Handoff 的载体。

决策 1 关键：handoff 时只传**结构化 ticket summary** 而非整段对话，
配合 LangGraph state checkpointing 实现：
  1. ~60% token 降幅
  2. 中断恢复（用户回来续聊 / Agent crash 重连 / 跨班次接力）
"""

from __future__ import annotations

from typing import Annotated, Literal, TypedDict

from langgraph.graph.message import add_messages

AgentName = Literal["triage", "billing", "technical", "escalation"]


class TicketSummary(TypedDict, total=False):
    """跨 Agent handoff 的结构化载荷 — 不传整段对话。"""

    intent: str
    customer_id: str
    tenant_id: str
    entities: dict[str, object]  # eg. {"charge_id": "...", "amount": 99}
    sla_tier: str
    confidence: float


class GraphState(TypedDict, total=False):
    # LangGraph 内置 messages reducer
    messages: Annotated[list, add_messages]

    # 多租户 / 多客户隔离 key（决策 4 · Layer 4）
    tenant_id: str
    customer_id: str
    thread_id: str

    # 当前路由到的 Agent
    current_agent: AgentName

    # 跨 Agent handoff 载荷（结构化）
    ticket_summary: TicketSummary

    # Plan-and-Execute 计划（决策 1）
    plan: list[str]

    # 工具调用 trace（流给前端 + 给 EvalGate）
    tool_calls: list[dict[str, object]]

    # Guardrails 标记
    guardrail_flags: list[str]

    # 业务 Agent 请求转人工 → Supervisor 路由到 escalation 节点（真接力，非文字建议）
    escalate: bool
