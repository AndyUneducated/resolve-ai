# Milestone 3 — Technical implementation plan

**Status:** Implemented (see [roadmap.md](roadmap.md) Milestone 3).

**Goal:** Ship the rest of the MCP-native tool layer. Promote Zendesk / Slack /
Salesforce / Intercom from `TOOLS`-only stubs to real stdio MCP servers
(matching the M2 Stripe template), wrap discovery behind a `ToolBelt`, and
extend the capability gate from `destructive` to **write + destructive**
(default-deny). No external SaaS credentials required — every server keeps the
"mock-first, deterministic in-memory store" pattern from `mcp_servers.stripe`.

---

## 1. Industry alignment (2026 defaults)

| Subsystem | Choice | Rationale |
|-----------|--------|-----------|
| Tool protocol | **Anthropic MCP** (`mcp.server.lowlevel.Server` + `stdio_server`) | De-facto standard since 2025 (Anthropic + OpenAI + Cursor + Bedrock) |
| Client bridge | `langchain-mcp-adapters` `MultiServerMCPClient.get_tools()` | Official LangChain ↔ MCP adapter; auto-converts tools to `BaseTool` |
| Tool registry | New `ToolBelt` class wrapping `loader.py` | Single source of truth + per-agent filtering + JSON manifest |
| Capability policy | **read / write / destructive**, default-deny on the last two | Aligns with OpenAI tool calling + Anthropic computer-use guidance + Bedrock Agents IAM model |
| Mock servers | Stripe-template (`server.py` + `data.py` + `reset_store()`) | Deterministic; identical contract to a future real adapter |

The 2-line elevator pitch:

> *Every SaaS the agents touch is reached through MCP; the API discovers tools
> at startup, annotates them with `(server, capability, full_name)`, and the
> Executor refuses any `write` or `destructive` call that an agent has not
> explicitly granted itself.*

---

## 2. Deliverables

| # | Item | Notes |
|---|------|-------|
| 1 | 4 new MCP servers (Zendesk / Slack / Salesforce / Intercom) | Mirror Stripe layout; deterministic seed data; failure paths |
| 2 | `mcp.toolbelt.ToolBelt` | Discovery + per-agent slice + capability lookup + manifest |
| 3 | Executor three-tier capability gate | `write` and `destructive` require explicit grant; `read` default-allow |
| 4 | Tests: per-server happy + error paths, multi-server discovery, capability matrix | Hermetic, no network |
| 5 | `Technical` + `Escalation` agents reuse the billing Plan-Execute-Replan subgraph | Each agent gets its own sliced toolbelt |
| 6 | Updated `.env.example` enabling all 5 `MCP_*_CMD` | Stripe-only fallback documented |

---

## 3. Server-by-server contract

Each new package mirrors `packages/mcp-servers/stripe/`:

```
packages/mcp-servers/<name>/
  pyproject.toml
  src/mcp_servers/<name>/
    __init__.py
    __main__.py        # python -m mcp_servers.<name>
    server.py          # @server.list_tools / @server.call_tool
    data.py            # in-memory store + reset_store()
```

Capability triage covers every roadmap follow-on (Escalation / Technical):

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

Each server must include:

- a `*_not_found` error path,
- an idempotent state transition (re-escalating an already-escalated ticket
  raises `already_escalated`, etc.),
- a `reset_store()` hook autoused by `apps/api/tests/conftest.py`.

---

## 4. `ToolBelt` design

`apps/api/src/resolveai_api/mcp/toolbelt.py`:

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

Lifespan reduces to:

```python
toolbelt = await ToolBelt.from_settings()
app.state.supervisor = SupervisorGraph(checkpointer=checkpointer, toolbelt=toolbelt)
```

`SupervisorGraph._build_agents()` switches from `filter_by_whitelist(mcp_tools, …)`
to `toolbelt.for_agent(WHITELIST)`. No agent code references `loader.py`.

---

## 5. Capability policy upgrade

Current state ([`core/executor.py`](../apps/api/src/resolveai_api/core/executor.py)) only
blocks `destructive`. After M3:

| Capability | Default | Requirement |
|------------|---------|-------------|
| `read` | allow | — |
| `write` | **deny** | `full_name` must be in the agent whitelist |
| `destructive` | **deny + audit** | whitelist + log entry on every call |

Errors keep the existing `PermissionError(f"{capability} tool {full!r} not granted (whitelist=...)")`
shape so the surrounding `try/except` in the billing subgraph still records
them as `past_steps` observations.

---

## 6. Tests (added in M3)

| File | What it locks down |
|------|--------------------|
| `apps/api/tests/test_zendesk_mcp.py` | `list_tools`, ticket read, update, escalate happy + sad |
| `apps/api/tests/test_slack_mcp.py` | `notify_team` happy + mention parsing |
| `apps/api/tests/test_salesforce_mcp.py` | account fetch + opportunity update |
| `apps/api/tests/test_intercom_mcp.py` | conversation fetch + tag user |
| `apps/api/tests/test_toolbelt.py` | `ToolBelt.from_settings()` discovers all 5 servers; manifest fields present |
| `apps/api/tests/test_capability_whitelist.py` (extended) | `write` denied without grant; `destructive` audited |

All tests stay hermetic — no real network, no external SaaS calls.

---

## 7. Delivery checklist

- [x] **zendesk** — real stdio server + `data.py` + tests
- [x] **slack** — real stdio server + `data.py` + tests
- [x] **salesforce** — real stdio server + `data.py` + tests
- [x] **intercom** — real stdio server + `data.py` + tests
- [x] **toolbelt** — `ToolBelt` class + lifespan wiring + `SupervisorGraph` plumbing
- [x] **capability** — Executor enforces `write` + `destructive`; `audit=True` on destructive
- [x] **agents** — Escalation runs deterministic Slack + Zendesk handoff; Technical pulls Zendesk ticket history (Plan-Execute-Replan reuse deferred to M6 once KB retrieval lands)
- [x] **multi-loader** — `test_toolbelt.py::test_from_settings_discovers_all_five_servers` proves 5 servers discoverable end-to-end
- [x] **env** — `.env.example` lists all 5 `MCP_*_CMD` enabled by default; `conftest.py` resets every store autouse
- [x] **verify** — `uv run python -m pytest` green (65 tests); roadmap M3 ticked

---

## 8. Non-goals (defer to later milestones)

- Real HTTP calls to Zendesk / Slack / Salesforce / Intercom (key-gated `live`
  mode is a follow-on; see M9 / dedicated milestone). MCP is intentionally
  designed so swapping `data.py` for a `client_real.py` later does not touch
  any agent or executor code.
- Streaming tool responses (MCP supports it; not needed yet).
- The `/admin/tools` UI surface (Phase 2 — `manifest()` already returns the
  JSON it would consume).
