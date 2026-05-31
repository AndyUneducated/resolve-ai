# `resolveai-api` — Backend

FastAPI + LangGraph 多 Agent 编排服务。

## 请求如何流经各模块

```mermaid
flowchart LR
  http["api/<br/>HTTP · SSE 路由"] --> sup["agents/<br/>Supervisor + 4 Agent"]
  sup --> core["core/<br/>Planner · Memory · Tool · Executor"]
  core --> mcp["mcp/<br/>MCP 工具协议"]
  core --> ret["retrieval/<br/>Hybrid retrieval"]
  gr["guardrails/<br/>四层 defense-in-depth"] -.->|"包裹 输入/输出/执行/记忆"| sup
  obs["observability/<br/>OTel + EvalGate"] -.->|trace| sup
```

## 模块对照

| 目录 | 设计文档章节 |
|---|---|
| `agents/` | §1.4 4 个 Agent 分工 + §2.2 Agent 编排 |
| `core/` | §1.4 Planner / Memory / Tool / Executor 四件套 |
| `guardrails/` | §2.3 决策 4 — 四层 defense-in-depth |
| `mcp/` | §2.3 决策 3 — MCP 工具协议 |
| `retrieval/` | §2.2 Hybrid retrieval (BM25 + dense + RRF + reranker) |
| `observability/` | §2.2 OTel + EvalGate 接入 |
| `api/` | HTTP/SSE 路由（`/api/v1/chat` 等） |

## 本地启动

```bash
uv run uvicorn resolveai_api.main:app --reload
# Swagger: http://localhost:8000/docs
```
