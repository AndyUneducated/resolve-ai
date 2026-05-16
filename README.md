# Adversarially-Hardened Multi-Agent Customer Support

[![CI](https://github.com/AndyUneducated/resolve-ai/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/AndyUneducated/resolve-ai/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![LangGraph](https://img.shields.io/badge/LangGraph-orchestration-1C3C3C?logo=langchain&logoColor=white)](https://github.com/langchain-ai/langgraph)
[![MCP](https://img.shields.io/badge/MCP-tools-7C3AED)](https://modelcontextprotocol.io/)
[![Next.js](https://img.shields.io/badge/Next.js-14-000000?logo=nextdotjs&logoColor=white)](https://nextjs.org/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5-3178C6?logo=typescript&logoColor=white)](https://www.typescriptlang.org/)
[![Postgres](https://img.shields.io/badge/Postgres-pgvector-4169E1?logo=postgresql&logoColor=white)](https://github.com/pgvector/pgvector)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)](https://docs.docker.com/compose/)

> 内部代号 **ResolveAI** — Sierra / Decagon 风格的客服多 Agent 系统：4 个专项 Agent 接力处理 ticket，5 个 SaaS 工具走 MCP 协议，T&S 背景背书的四层 adversarial guardrails。

## 架构一览

```
Frontend (Next.js + shadcn/ui)
        │  SSE
        ▼
FastAPI ──► LangGraph Supervisor
                 │
   ┌─────────────┼─────────────┬──────────────┐
   ▼             ▼             ▼              ▼
Triage       Billing       Technical     Escalation
 Agent        Agent          Agent         Agent
   │             │             │              │
   └──── Planner / Memory / Tool / Executor ──┘
                       │
               MCP Tool Registry
                       │
   Zendesk / Stripe / Slack / Salesforce / Intercom (mock)
                       │
              gVisor Sandbox (per-call)

四层 Guardrails: Input → Exec → Output → Memory
```

更完整的设计文档见 [`docs/design.md`](docs/design.md)。

## 仓库结构

```
resolve-ai/
├── apps/
│   ├── api/                # FastAPI + LangGraph 后端
│   └── web/                # Next.js 前端 (聊天 UI + tool trace)
├── packages/
│   └── mcp-servers/        # 5 个 mock SaaS 的 MCP server
│       ├── zendesk/
│       ├── stripe/
│       ├── slack/
│       ├── salesforce/
│       └── intercom/
├── infra/
│   ├── docker/             # Dockerfile / gVisor 配置
│   └── k8s/                # (stretch) EKS manifests
├── scripts/
│   ├── seed_db.py          # 初始化 FAQ / runbook / 演示 ticket
│   └── red_team.py         # 200 个 adversarial prompt 测试 harness
├── e2e_tests/              # 端到端集成测试（与 apps/api/tests 命名隔离，避免 pytest 冲突）
├── docs/
│   └── design.md           # 完整设计文档
├── docker-compose.yml      # Postgres+pgvector / Redis / 本地 MCP servers
├── Makefile                # 一键 dev / lint / test
├── pyproject.toml          # Python workspace (uv) 根配置
└── .env.example
```

## 快速开始

### 0. 前置要求

- Python 3.11+ （推荐用 [`uv`](https://docs.astral.sh/uv/)）
- Node.js 20+ （前端）
- Docker / Docker Compose （Postgres + pgvector）

### 1. 安装依赖

```bash
# 后端 + MCP servers (uv workspace)
uv sync

# 前端
cd apps/web && npm install && cd -
```

### 2. 起依赖服务

```bash
cp .env.example .env
docker compose up -d postgres
make seed
```

### 3. 启动 dev 环境

```bash
make dev
# 后端: http://localhost:8000  (Swagger: /docs)
# 前端: http://localhost:3000
```

### 4. 跑红队测试

```bash
make red-team   # 200 个 adversarial prompt，期望 0 PII leak
```

## 关键技术点（面试 talking point）

1. **4-Agent + Plan-and-Execute + Stateful Handoff + Cost Routing** — 业务 Agent 多步规划批量执行；handoff 只传结构化 ticket summary（降 60%+ token）；Triage 用 Haiku，专项 Agent 用 Sonnet。
2. **MCP-native 工具层** — 5 个 SaaS 全走 MCP，新增 SaaS = 加一个 MCP server。
3. **gVisor per-call 沙箱** — 工具调用是不可信代码，syscall-level 隔离。
4. **四层 Adversarial Guardrails (defense-in-depth)** — input (Llama Guard + indirect injection + Presidio) / exec (gVisor + capability whitelist) / output (Presidio re-scan + policy check + hallucinated entity detector) / memory (per-tenant + per-customer state isolation)。
5. **Chaos demo** — 5K mock ticket 并发，P95 < 6s，0 PII leak。

## 项目状态

🚧 **Scaffold 阶段** — 当前提交搭建了完整的目录骨架与可运行 hello-world endpoint，下一步按 [`docs/roadmap.md`](docs/roadmap.md) 逐模块填充实现。

## License

MIT
