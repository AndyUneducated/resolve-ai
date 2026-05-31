# Milestone 3 — 技术实现方案

**Status:** 已实现（见 [roadmap.md](roadmap.md) Milestone 3）。

**Goal:** 交付 MCP-native tool layer 的剩余部分。将 Zendesk / Slack / Salesforce / Intercom 从仅 `TOOLS` 的 stub 提升为真实 stdio MCP server（对齐 M2 Stripe 模板），用 `ToolBelt` 包装 discovery，并将 capability gate 从 `destructive` 扩展到 **write + destructive**（default-deny）。无需外部 SaaS credentials — 每个 server 保持 `mcp_servers.stripe` 的 “mock-first, deterministic in-memory store” 模式。

---

## 1. 行业对齐（2026 默认选型）

| 子系统 | 选型 | 理由 |
|-----------|--------|-----------|
| Tool protocol | **Anthropic MCP**（`mcp.server.lowlevel.Server` + `stdio_server`） | 2025 起 de-facto standard（Anthropic + OpenAI + Cursor + Bedrock） |
| Client bridge | `langchain-mcp-adapters` `MultiServerMCPClient.get_tools()` | 官方 LangChain ↔ MCP adapter；自动转换为 `BaseTool` |
| Tool registry | 新 `ToolBelt` class 包装 `loader.py` | Single source of truth + per-agent filtering + JSON manifest |
| Capability policy | **read / write / destructive**，后两者 default-deny | 对齐 OpenAI tool calling + Anthropic computer-use guidance + Bedrock Agents IAM model |
| Mock servers | Stripe-template（`server.py` + `data.py` + `reset_store()`） | Deterministic；与未来 real adapter 契约相同 |

两行 elevator pitch：

> *Agents 接触的每个 SaaS 都经 MCP 到达；API 在 startup 时 discovery tools，标注 `(server, capability, full_name)`，Executor 拒绝任何 agent 未显式 grant 的 `write` 或 `destructive` 调用。*

---

## 2. 交付物

| # | Item | Notes |
|---|------|-------|
| 1 | 4 个新 MCP server（Zendesk / Slack / Salesforce / Intercom） | 镜像 Stripe layout；deterministic seed data；failure paths |
| 2 | `mcp.toolbelt.ToolBelt` | Discovery + per-agent slice + capability lookup + manifest |
| 3 | Executor 三档 capability gate | `write` 和 `destructive` 需显式 grant；`read` default-allow |
| 4 | Tests：per-server happy + error paths、multi-server discovery、capability matrix | Hermetic，无 network |
| 5 | `Technical` + `Escalation` agents 复用 billing Plan-Execute-Replan subgraph | 每个 agent 获得 sliced toolbelt |
| 6 | 更新 `.env.example` 启用全部 5 个 `MCP_*_CMD` | 文档说明 Stripe-only fallback |

---

## 3. 逐 server 契约

每个新 package 镜像 `packages/mcp-servers/stripe/`：

```
packages/mcp-servers/<name>/
  pyproject.toml
  src/mcp_servers/<name>/
    __init__.py
    __main__.py        # python -m mcp_servers.<name>
    server.py          # @server.list_tools / @server.call_tool
    data.py            # in-memory store + reset_store()
```

Capability triage 覆盖 roadmap 后续（Escalation / Technical）：

| Server | Tool | Capability | Consumer |
|--------|------|------------|----------|
| **Zendesk** | `get_ticket_history(customer_id)` | `read` | Billing / Technical |
| | `update_ticket(ticket_id, status?, note?)` | `write` | Billing / Technical |
| | `escalate(ticket_id, reason)` | `destructive` | Escalation |
| **Slack** | `notify_team(channel, message, mention?)` | `write` | Escalation |
| | `post_message(channel, message)` | `write` | Internal collab |
| **Salesforce** | `get_account(customer_id)` | `read` | Billing |
| | `update_opportunity(opportunity_id, stage?, amount?)` | `write` | Billing |
| **Intercom** | `get_conversation(conversation_id)` | `read` | Technical |
| | `tag_user(user_id, tag)` | `write` | Technical |

每个 server 必须包含：

- `*_not_found` error path，
- idempotent state transition（如 re-escalating 已 escalated ticket 抛出 `already_escalated` 等），
- `reset_store()` hook，由 `apps/api/tests/conftest.py` autouse。

---

## 4. `ToolBelt` 设计

`apps/api/src/resolveai_api/mcp/toolbelt.py`：

```python
class ToolBelt:
    """Single source of truth for MCP-backed LangChain tools."""

    def __init__(self, tools: list[BaseTool]) -> None: ...

    @classmethod
    async def from_settings(cls) -> "ToolBelt":
        """Build MultiServerMCPClient from registry, discover, annotate."""

    def for_agent(self, whitelist: list[str]) -> list[BaseTool]:
        """Filter by metadata.full_name (replaces filter_by_whitelist)."""

    def by_capability(self, capability: str) -> list[BaseTool]: ...

    def manifest(self) -> list[dict]:
        """Serializable view for /admin and ablation logs."""
```

Lifespan 简化为：

```python
toolbelt = await ToolBelt.from_settings()
app.state.supervisor = SupervisorGraph(checkpointer=checkpointer, toolbelt=toolbelt)
```

`SupervisorGraph._build_agents()` 从 `filter_by_whitelist(mcp_tools, …)` 切换为 `toolbelt.for_agent(WHITELIST)`。Agent 代码不再引用 `loader.py`。

---

## 5. Capability policy 升级

当前状态（[`core/executor.py`](../apps/api/src/resolveai_api/core/executor.py)）仅 block `destructive`。M3 之后：

| Capability | Default | Requirement |
|------------|---------|-------------|
| `read` | allow | — |
| `write` | **deny** | `full_name` 必须在 agent whitelist 中 |
| `destructive` | **deny + audit** | whitelist + 每次调用写 log entry |

Errors 保持现有 `PermissionError(f"{capability} tool {full!r} not granted (whitelist=...)")` 形态，billing subgraph 外围 `try/except` 仍将其记为 `past_steps` observations。

---

## 6. Tests（M3 新增）

| File | 锁定行为 |
|------|--------------------|
| `apps/api/tests/test_zendesk_mcp.py` | `list_tools`、ticket read、update、escalate happy + sad |
| `apps/api/tests/test_slack_mcp.py` | `notify_team` happy + mention parsing |
| `apps/api/tests/test_salesforce_mcp.py` | account fetch + opportunity update |
| `apps/api/tests/test_intercom_mcp.py` | conversation fetch + tag user |
| `apps/api/tests/test_toolbelt.py` | `ToolBelt.from_settings()` 发现全部 5 个 server；manifest fields 齐全 |
| `apps/api/tests/test_capability_whitelist.py`（扩展） | 无 grant 时 `write` denied；`destructive` audited |

所有测试保持 hermetic — 无真实 network、无外部 SaaS calls。

---

## 7. 交付 checklist

- [x] **zendesk** — 真实 stdio server + `data.py` + tests
- [x] **slack** — 真实 stdio server + `data.py` + tests
- [x] **salesforce** — 真实 stdio server + `data.py` + tests
- [x] **intercom** — 真实 stdio server + `data.py` + tests
- [x] **toolbelt** — `ToolBelt` class + lifespan wiring + `SupervisorGraph` plumbing
- [x] **capability** — Executor 强制 `write` + `destructive`；destructive 上 `audit=True`
- [x] **agents** — Escalation 跑 deterministic Slack + Zendesk handoff；Technical 拉 Zendesk ticket history（Plan-Execute-Replan 复用延至 M6 KB retrieval 落地后）
- [x] **multi-loader** — `test_toolbelt.py::test_from_settings_discovers_all_five_servers` 证明 5 server 端到端可 discovery
- [x] **env** — `.env.example` 默认列出全部 5 个 `MCP_*_CMD`；`conftest.py` autouse 重置每个 store
- [x] **verify** — `uv run python -m pytest` 绿（65 tests）；roadmap M3 打勾

---

## 8. Non-goals（延至后续 milestone）

- 对 Zendesk / Slack / Salesforce / Intercom 的真实 HTTP calls（key-gated `live` mode 为 follow-on；见 M9 / dedicated milestone）。MCP 有意设计成后续把 `data.py` 换成 `client_real.py` 也不动 agent 或 executor 代码。
- Streaming tool responses（MCP 支持；暂不需要）。
- `/admin/tools` UI surface（Phase 2 — `manifest()` 已返回其将消费的 JSON）。
