# 设计文档

完整设计文档在仓库外：`projects/02-resolveai.md`（不入仓库，避免污染产品代码与简历仓库）。

仓库内只保留**实现到位的**设计决策抽象。每个模块的 docstring 直接对应设计文档的章节：

| 设计章节 | 仓库实现位置 |
|---|---|
| §1.4 4 个 Agent 分工 | `apps/api/src/resolveai_api/agents/{triage,billing,technical,escalation}.py` |
| §1.4 Planner / Memory / Tool / Executor 四件套 | `apps/api/src/resolveai_api/core/` |
| §1.4 Stateful Handoff（结构化 ticket summary） | `apps/api/src/resolveai_api/agents/state.py` `TicketSummary` |
| §2.2 LangGraph supervisor | `apps/api/src/resolveai_api/agents/supervisor.py` |
| §2.3 决策 1 · Cost-aware Routing | `apps/api/src/resolveai_api/core/router.py` |
| §2.3 决策 3 · MCP 工具协议 | `apps/api/src/resolveai_api/mcp/` + `packages/mcp-servers/*` |
| §2.3 决策 4 · 四层 Guardrails | `apps/api/src/resolveai_api/guardrails/` |
| §2.2 Hybrid retrieval | `apps/api/src/resolveai_api/retrieval/` |
| §2.2 OTel + EvalGate | `apps/api/src/resolveai_api/observability/` |
