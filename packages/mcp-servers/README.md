# Mock MCP Servers

5 个 SaaS 工具的 mock，全部按 [Model Context Protocol](https://modelcontextprotocol.io) 规范暴露：

| Server | 工具样例 | 调用方 |
|---|---|---|
| `zendesk` | `get_ticket_history`, `update_ticket`, `escalate` | Billing / Technical / Escalation |
| `stripe` | `list_charges`, `get_charge`, `refund` | Billing |
| `slack` | `notify_team`, `post_message` | Escalation |
| `salesforce` | `get_account`, `update_opportunity` | Billing / Technical |
| `intercom` | `get_conversation`, `tag_user` | Technical |

每个 server 独立 package，命名空间 `mcp_servers.<name>`。

## 协议

- dev：stdio（`python -m mcp_servers.stripe`）
- prod：HTTP/SSE（套到 gVisor pod 里跑）

## 新增一个 SaaS

1. 复制 `stripe/` 目录并改名。
2. 改 `pyproject.toml` 的 package name + entry point。
3. 在 [`apps/api/.../mcp/registry.py`](../../apps/api/src/resolveai_api/mcp/registry.py) 加一行注册。
4. 在对应 Agent 的 `TOOL_WHITELIST` 里加白名单（即护栏 Layer 2 的 capability 白名单）。

整个过程**不需要改 Agent 代码本身**——这正是走 MCP 协议带来的收益。
