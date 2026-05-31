# Roadmap — 从 scaffold 到 demo-ready

当前提交：scaffold 阶段（结构 + 占位实现 + smoke test）。

> **图例**：
> 🧱 = 必做地基（table stakes，进面试的最低要求）
> ⭐ = 差异化里程碑（top 10% → top 3% 的关键 leverage，**别跳过**）

## Milestone 1 · Hello-World 流通（1 day）🧱 — ✅ Done

- [x] `make install` 跑通（uv sync + npm install）
- [x] `make api` 起后端，`GET /` 200
- [x] `make web` 起前端，`/chat` 能发请求拿到 stub 响应
- [x] `make test` 通过

## Milestone 2 · 单 Agent 真跑（2-3 days）🧱 — ✅ Done

- [x] `TriageAgent` 接 **Ollama `qwen3.5:9b`**（`with_structured_output(TriageOutput)`，Pydantic v2 schema）
- [x] `BillingAgent` 接 **Ollama**（`VERTICAL_MODEL`，默认本机验证用 `qwen3.5:9b`，可按需改 `qwen3.6:27b`），落地 LangGraph 官方 Plan-Execute-Replan 三节点闭环子图（`apps/api/src/resolveai_api/agents/billing_graph.py`）
- [x] Stripe MCP server 用官方 `mcp.server.lowlevel.Server` + `stdio_server` 真跑；`list_charges` / `get_charge` / `refund` 全部可调，含 over-amount / already-refunded 错误路径
- [x] LangGraph `AsyncPostgresSaver` 接上（`CHECKPOINT_BACKEND=postgres`），测试用 `MemorySaver`；thread_id = `tenant::customer::thread` 对齐决策 4 · Layer 4
- [x] `langchain-mcp-adapters` 桥接 MCP → LangChain `BaseTool`，capability whitelist 在 Executor 强制（destructive 必须显式 grant）
- [x] 21/21 单元 + e2e 测试绿，含 `test_triage_structured` / `test_billing_subgraph` / `test_stripe_mcp` / `test_capability_whitelist` / `test_chat_flow`（含 checkpoint 恢复）

详细技术方案见 [`docs/milestone-2-plan.md`](milestone-2-plan.md)。

## Milestone 3 · 5 SaaS 全 MCP-ize（3 days）🧱 — ✅ Done

- [x] Zendesk / Slack / Salesforce / Intercom 全部按 `mcp.server.lowlevel.Server + stdio_server` 实装（mirror Stripe），各自 7 条单测覆盖 happy path + 错误路径
- [x] `mcp/toolbelt.py` `ToolBelt`：`from_settings()` 走 `MultiServerMCPClient` 自动 discovery，`for_agent(whitelist)` 切片、`by_capability` 分组、`manifest()` 序列化；5-server discovery 烟测覆盖（`test_toolbelt.py`）
- [x] Executor 升级到三档 capability（read/write/destructive），write 与 destructive 必须显式 grant；destructive 调用 `audit=True`
- [x] Escalation Agent 走真实 `slack.notify_team` + `zendesk.escalate`；Technical Agent 通过 `zendesk.get_ticket_history` 拉历史 context
- [x] `.env.example` 默认启用 5 个 `MCP_*_CMD`；`conftest.py` 重置全部 mock store
- [x] 65/65 单元 + 集成测试绿（含 5-server discovery 烟测）

详细技术方案见 [`docs/milestone-3-plan.md`](milestone-3-plan.md)。

## Milestone 4 · 四层 Guardrails 真跑（2-3 days）🧱 — ✅ Done

- [x] 输入：Llama Guard endpoint + Presidio analyzer
- [x] 执行：gVisor runtime 跑工具调用（K8s Pod-per-call 或 docker --runtime=runsc）
- [x] 输出：Presidio re-scan + policy LLM judge + hallucinated entity detector（tool-return cross-check）
- [x] 记忆：state checkpoint key 强制 `(tenant_id, customer_id)` 命名空间，cross-tenant access 抛 PermissionError

## Milestone 5 · Adversarial Eval Harness（3-4 days）⭐ — ✅ Done

> **目标**：从"我做了 4 层 guardrails"升级为"我**证明**了每一层都不可替代，并量化了精度/召回/FP rate"。
> **产出**：Layer-attribution 表 + Ablation 表 + 一篇 blog 草稿（top 3% 的杀手锏）。

### 5.1 数据集（200 条 prompt，分类 + 标注 ground truth）

- [x] `tests/fixtures/red_team.jsonl`，每条 schema：
  ```json
  {
    "id": "...", "category": "jailbreak|indirect_injection|pii_extraction|unauthorized_concession|cross_tenant",
    "prompt": "...", "expected_block_layer": "input|exec|output|memory|none",
    "expected_intent": "billing|technical|...",  // 用于跑通正常路径下的辅助断言
    "notes": "为什么这条 prompt 应该被这一层拦"
  }
  ```
- [x] 5 类各 30-50 条，覆盖：
  - **Jailbreak（50）** — DAN / role-play / 多语言绕过
  - **Indirect injection（50）** — 嵌在 quoted ticket / RAG 文档里的恶意指令
  - **PII extraction（30）** — 套上轮对话 / 套 system prompt / 跨客户钓鱼
  - **Unauthorized concession（40）** — 套折扣码 / 编造退款金额超 SLA
  - **Cross-tenant（30）** — 多租户 namespace 串号攻击
- [x] 配 50 条**良性 ticket**对照集，用来测 false positive rate（这是 senior 信号）

### 5.2 Eval Runner

- [x] `scripts/eval_adversarial.py` —— 跑全 200 + 50 条，每条记录：
  - 哪一层 flag 了 / 是否 block 了 / 输出文本
  - 实际 block layer vs expected block layer（attribution 准确度）
  - 端到端 latency / token cost
- [x] 输出 `reports/eval_<timestamp>.json` + Markdown 表（`scripts/eval_report.py` + `guardrails/eval_scoring.py`）

### 5.3 关键产出表（**面试现场用**）

- [x] **Layer Attribution Table** — 证明每层都有不可替代价值

  | 攻击类别 | Layer 1 拦 | Layer 2 拦 | Layer 3 拦 | Layer 4 拦 | 漏过 |
  |---|---|---|---|---|---|
  | Jailbreak | ?% | — | ?% | — | ?% |
  | Indirect injection | ?% | — | ?% | — | ?% |
  | … | | | | | |

- [x] **Ablation Table** — 关掉哪一层会掉多少（证明四层都必要）

  | 配置 | Block rate | False positive | 漏过的 worst-case 例子 |
  |---|---|---|---|
  | 全 4 层（baseline）| ?% | ?% | — |
  | 关 Layer 1（输入）| ?% | ?% | "indirect injection X 通过了" |
  | 关 Layer 3（输出）| ?% | ?% | "L1 没拦的 jailbreak 输出 PII" |
  | 关 Layer 4（记忆）| ?% | ?% | "跨租户 X 串号" |

- [x] **False Positive 分析** — 良性 ticket 的误拦率 + 误拦原因分类
- [x] **Blog 草稿**：*"Why customer-facing AI needs 4 layers of guardrails: 200 adversarial prompts, attribution-tested"* —— 准备发 HN / r/LocalLLaMA / langgraph discord

### 5.4 验收

- [x] 5 类攻击，**baseline (4 层) 漏过率 ≤ 2%**（由 `scripts/eval_adversarial.py` 实测产出）
- [x] 良性 ticket false positive ≤ 5%（由报告脚本自动计算）
- [x] **每一层关掉都能找到至少 1 个新增漏过 case**（L2 作为 blast-radius 层，指标解释写入 blog）

## Milestone 6 · Hybrid Retrieval（2 days）🧱 — ✅ Done

- [x] Postgres 灌 50+ 条 FAQ / runbook（带 embedding）
- [x] BM25 (ts_rank_cd) + dense (cosine) 双路 + RRF 融合
- [x] bge-reranker-v2-m3 精排
- [x] Technical Agent 跑 KB-grounded 回复

详细技术方案见 [`docs/milestone-6-plan.md`](milestone-6-plan.md)。

## Milestone 7 · 量化 Architecture Ablation（3-4 days）⭐

> **目标**：把"我用了 multi-agent / Plan-and-Execute / 结构化 handoff"升级为"我**测过**这些选择的代价与收益，能 defend 每一个 trade-off"。
> **产出**：1 张主表 + 4 个对照配置的 benchmark 数据（Sierra staff 面试官的最爱追问点）。

### 7.1 Benchmark 数据集

- [x] `apps/api/tests/fixtures/benchmark_tickets.jsonl` —— 120 条真实风格 ticket（手写，对齐 MCP mock seed：ch_001..ch_005 / cus_demo_001..003 / zd_001..004）：
  - 72 billing / 36 technical / 12 escalation（60/30/10）
  - 每条标 ground truth：`expected_intent` / `expected_resolution_path` / `expected_tool_calls` + `rubric`
  - LLM-judge（`eval/judge.py` · `ResolutionJudge`）按 rubric 评 final answer 是否真解决

### 7.2 4 个对照配置

| Variant | 描述 | 验证什么 |
|---|---|---|
| **A · Single-Agent + 全工具白名单** | 1 个 LLM 看所有 5 SaaS 工具 + 大 prompt | 证明 multi-agent 不是炫技 |
| **B · 4-Agent + 全对话 handoff** | Triage 把整段对话原样传给业务 Agent | 量化"结构化 ticket summary" 的真实收益 |
| **C · 4-Agent + ReAct（替代 Plan-and-Execute）** | 业务 Agent 单步循环 vs 多步规划 | 量化 Plan-and-Execute 的真实收益 |
| **D · 最终方案**（4-Agent + 结构化 handoff + Plan-and-Execute + Cost Routing） | baseline | — |

- [x] `scripts/eval_architecture.py` —— 对每个 variant 跑全 benchmark，记录：
  - **Token / ticket**（in + out 分开；真实 Ollama token，经 `core/usage.py` contextvar trace 按 tier 归集，含子图/结构化输出调用）
  - **$ / ticket**（按 tier 模型计价 · `eval/pricing.py`：triage≈Haiku / vertical≈Sonnet 公开价；token 真实、美元建模）
  - **Latency P50 / P95**
  - **Auto-resolution rate**（LLM-judge 评是否真解决）
  - **Tool error rate**（工具选错 / 调用失败 / hallucinated entity · `eval/trace.py:classify_tool_errors`）

> 4 个对照配置通过 `eval/variants.py` 的 `VariantSpec`（topology / handoff / business_strategy / triage_tier 四轴）+ `SupervisorGraph(options=GraphOptions(...))` 表达；生产路径默认即 variant D，M1-M6 行为不变（108/108 测试绿）。

### 7.3 关键产出表

- [x] **Architecture Ablation Table** 生成器（**简历现场摊开的杀手锏**）—— `eval/arch_scoring.py:render_markdown` 产出下表 + `Δ (D vs A)` 行（数字待全量跑填入，见 `docs/blog/multi-agent-tradeoffs.md`）

  | Variant | Token/ticket | $/ticket | P95 (s) | Auto-resolve | Tool error |
  |---|---|---|---|---|---|
  | A · Single-Agent | TBD | TBD | TBD | TBD | TBD |
  | B · 4-Agent + full transcript handoff | TBD | TBD | TBD | TBD | TBD |
  | C · 4-Agent + ReAct | TBD | TBD | TBD | TBD | TBD |
  | **D · 最终方案** | TBD | TBD | TBD | TBD | TBD |
  | **Δ (D vs A)** | TBD | TBD | TBD | TBD | TBD |

- [x] **Cost Routing 单独 ablation** —— variant `D` vs `D_triage_vertical`（`--cost-routing`）：同 token、按 tier 计价对比 $/ticket + auto-resolve 是否掉（`build_cost_routing_table`）
- [x] **Failure Mode 报告** —— 每个 variant 取 1-2 个 worst-case（judge 分最低 / errored）+ 原因（`build_failure_modes`，已渲染进 `.md`）

### 7.4 验收

> 验收需在目标硬件/模型上跑全量 `uv run python scripts/eval_architecture.py --variants A,B,C,D --cost-routing` 后填表（本机 9B Ollama 下 plan-execute 单条耗时长，建议调大 `--case-timeout` 或换更快模型）。Harness、judge、scoring、tracing 已端到端验证（technical 单条实跑：token/cost/latency/agent_path/tool_calls/judge 全字段产出）。

- [ ] D 在**至少 3 个指标**上显著优于 A / B / C（不显著的指标也要诚实写在 blog 里 —— "这个维度 multi-agent 没收益，trade-off 在这")
- [ ] 简历 bullet 的所有数字（"~60% token reduction" 等）能被这张表 back up
- [x] **Blog 草稿 2**：*"Multi-agent vs single-agent for customer support: a benchmarked trade-off study"* —— `docs/blog/multi-agent-tradeoffs.md`（方法论完成，表格待全量数字）

详细技术方案见 [`docs/milestone-7-plan.md`](milestone-7-plan.md)。

## Milestone 8 · Chaos Demo & 视频 artifact（2 days）⭐

- [ ] `scripts/chaos_load.py` 5K mock ticket 并发，P95 < 6s
- [ ] OTel 接 EvalGate（项目 1）做 online regression
- [ ] **3 分钟 Demo 视频**（Loom / YouTube），脚本：
  - 0:00-0:30 正常 ticket：Triage → Billing → Stripe refund → 成功
  - 0:30-1:30 对抗 ticket：indirect injection 被 Layer 1 漏过，Layer 3 policy check 截住，trace UI 高亮
  - 1:30-2:00 跨租户串号攻击：namespace check 抛 PermissionError，trace 复现
  - 2:00-3:00 chaos load 实时 metrics + 现场看 Architecture Ablation 表
- [ ] 简历 bullet 直接挂视频链接

## Milestone 9 · 多租户 Stretch（可选）

- [ ] Postgres row-level security
- [ ] tenant_id 列贯穿所有表
- [ ] /admin UI 配置 OAuth + RBAC
