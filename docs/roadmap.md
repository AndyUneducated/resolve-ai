# Roadmap — 从 scaffold 到 demo-ready

当前提交：scaffold 阶段（结构 + 占位实现 + smoke test）。

> **图例**：
> 🧱 = 必做地基（table stakes，进面试的最低要求）
> ⭐ = 差异化里程碑（top 10% → top 3% 的关键 leverage，**别跳过**）

## Milestone 1 · Hello-World 流通（1 day）🧱

- [ ] `make install` 跑通（uv sync + npm install）
- [ ] `make api` 起后端，`GET /` 200
- [ ] `make web` 起前端，`/chat` 能发请求拿到 stub 响应
- [ ] `make test` 通过

## Milestone 2 · 单 Agent 真跑（2-3 days）🧱

- [ ] `TriageAgent` 接 Anthropic Haiku，做意图分类
- [ ] `BillingAgent` 接 Sonnet，跑 Plan-and-Execute
- [ ] 1 个 MCP server（Stripe）真跑，`stripe.list_charges` / `refund` 可调
- [ ] LangGraph PostgresSaver 接上，state 真持久化

## Milestone 3 · 5 SaaS 全 MCP-ize（3 days）🧱

- [ ] Zendesk / Slack / Salesforce / Intercom 全部按 MCP server 实现 + 测试
- [ ] `ToolBelt` 自动从 MCP discovery 拉 tool spec
- [ ] capability whitelist 起作用（write tool 必须 explicit grant）

## Milestone 4 · 四层 Guardrails 真跑（2-3 days）🧱

- [ ] 输入：Llama Guard endpoint + Presidio analyzer
- [ ] 执行：gVisor runtime 跑工具调用（K8s Pod-per-call 或 docker --runtime=runsc）
- [ ] 输出：Presidio re-scan + policy LLM judge + hallucinated entity detector（tool-return cross-check）
- [ ] 记忆：state checkpoint key 强制 `(tenant_id, customer_id)` 命名空间，cross-tenant access 抛 PermissionError

## Milestone 5 · Adversarial Eval Harness（3-4 days）⭐

> **目标**：从"我做了 4 层 guardrails"升级为"我**证明**了每一层都不可替代，并量化了精度/召回/FP rate"。
> **产出**：Layer-attribution 表 + Ablation 表 + 一篇 blog 草稿（top 3% 的杀手锏）。

### 5.1 数据集（200 条 prompt，分类 + 标注 ground truth）

- [ ] `tests/fixtures/red_team.jsonl`，每条 schema：
  ```json
  {
    "id": "...", "category": "jailbreak|indirect_injection|pii_extraction|unauthorized_concession|cross_tenant",
    "prompt": "...", "expected_block_layer": "input|exec|output|memory|none",
    "expected_intent": "billing|technical|...",  // 用于跑通正常路径下的辅助断言
    "notes": "为什么这条 prompt 应该被这一层拦"
  }
  ```
- [ ] 5 类各 30-50 条，覆盖：
  - **Jailcategoryreak（50）** — DAN / role-play / 多语言绕过
  - **Indirect injection（50）** — 嵌在 quoted ticket / RAG 文档里的恶意指令
  - **PII extraction（30）** — 套上轮对话 / 套 system prompt / 跨客户钓鱼
  - **Unauthorized concession（40）** — 套折扣码 / 编造退款金额超 SLA
  - **Cross-tenant（30）** — 多租户 namespace 串号攻击
- [ ] 配 50 条**良性 ticket**对照集，用来测 false positive rate（这是 senior 信号）

### 5.2 Eval Runner

- [ ] `scripts/eval_adversarial.py` —— 跑全 200 + 50 条，每条记录：
  - 哪一层 flag 了 / 是否 block 了 / 输出文本
  - 实际 block layer vs expected block layer（attribution 准确度）
  - 端到端 latency / token cost
- [ ] 输出 `reports/eval_<timestamp>.json` + Markdown 表

### 5.3 关键产出表（**面试现场用**）

- [ ] **Layer Attribution Table** — 证明每层都有不可替代价值

  | 攻击类别 | Layer 1 拦 | Layer 2 拦 | Layer 3 拦 | Layer 4 拦 | 漏过 |
  |---|---|---|---|---|---|
  | Jailbreak | ?% | — | ?% | — | ?% |
  | Indirect injection | ?% | — | ?% | — | ?% |
  | … | | | | | |

- [ ] **Ablation Table** — 关掉哪一层会掉多少（证明四层都必要）

  | 配置 | Block rate | False positive | 漏过的 worst-case 例子 |
  |---|---|---|---|
  | 全 4 层（baseline）| ?% | ?% | — |
  | 关 Layer 1（输入）| ?% | ?% | "indirect injection X 通过了" |
  | 关 Layer 3（输出）| ?% | ?% | "L1 没拦的 jailbreak 输出 PII" |
  | 关 Layer 4（记忆）| ?% | ?% | "跨租户 X 串号" |

- [ ] **False Positive 分析** — 良性 ticket 的误拦率 + 误拦原因分类
- [ ] **Blog 草稿**：*"Why customer-facing AI needs 4 layers of guardrails: 200 adversarial prompts, attribution-tested"* —— 准备发 HN / r/LocalLLaMA / langgraph discord

### 5.4 验收

- [ ] 5 类攻击，**baseline (4 层) 漏过率 ≤ 2%**
- [ ] 良性 ticket false positive ≤ 5%
- [ ] **每一层关掉都能找到至少 1 个新增漏过 case**（如果关掉 Layer X 没掉分，说明 X 设计冗余 → 必须 reflective 在 blog 里写明）

## Milestone 6 · Hybrid Retrieval（2 days）🧱

- [ ] Postgres 灌 50+ 条 FAQ / runbook（带 embedding）
- [ ] BM25 (ts_rank_cd) + dense (cosine) 双路 + RRF 融合
- [ ] bge-reranker-v2-m3 精排
- [ ] Technical Agent 跑 KB-grounded 回复

## Milestone 7 · 量化 Architecture Ablation（3-4 days）⭐

> **目标**：把"我用了 multi-agent / Plan-and-Execute / 结构化 handoff"升级为"我**测过**这些选择的代价与收益，能 defend 每一个 trade-off"。
> **产出**：1 张主表 + 4 个对照配置的 benchmark 数据（Sierra staff 面试官的最爱追问点）。

### 7.1 Benchmark 数据集

- [ ] `tests/fixtures/benchmark_tickets.jsonl` —— 100-200 条真实风格 ticket：
  - 60% billing / 30% technical / 10% escalation
  - 每条标 ground truth：expected_intent / expected_resolution_path / expected_tool_calls
  - 复用 EvalGate（项目 1）做 LLM-judge 评 final answer 质量

### 7.2 4 个对照配置

| Variant | 描述 | 验证什么 |
|---|---|---|
| **A · Single-Agent + 全工具白名单** | 1 个 LLM 看所有 5 SaaS 工具 + 大 prompt | 证明 multi-agent 不是炫技 |
| **B · 4-Agent + 全对话 handoff** | Triage 把整段对话原样传给业务 Agent | 量化"结构化 ticket summary" 的真实收益 |
| **C · 4-Agent + ReAct（替代 Plan-and-Execute）** | 业务 Agent 单步循环 vs 多步规划 | 量化 Plan-and-Execute 的真实收益 |
| **D · 最终方案**（4-Agent + 结构化 handoff + Plan-and-Execute + Cost Routing） | baseline | — |

- [ ] `scripts/eval_architecture.py` —— 对每个 variant 跑全 benchmark，记录：
  - **Token / ticket**（in + out 分开）
  - **$ / ticket**（按 variant 实际走的模型计价）
  - **Latency P50 / P95**
  - **Auto-resolution rate**（LLM-judge 评是否真解决）
  - **Tool error rate**（工具选错 / 参数错 / hallucinated entity）

### 7.3 关键产出表

- [ ] **Architecture Ablation Table**（**简历现场摊开的杀手锏**）

  | Variant | Token/ticket | $/ticket | P95 (s) | Auto-resolve | Tool error |
  |---|---|---|---|---|---|
  | A · Single-Agent | ? | ? | ? | ?% | ?% |
  | B · 4-Agent + full transcript handoff | ? | ? | ? | ?% | ?% |
  | C · 4-Agent + ReAct | ? | ? | ? | ?% | ?% |
  | **D · 最终方案** | ? | ? | ? | ?% | ?% |
  | **Δ (D vs A)** | -??% | -??% | -??% | +??pp | -??pp |

- [ ] **Cost Routing 单独 ablation** —— Triage 用 Sonnet vs Haiku 的 $/ticket 对比 + auto-resolve 是否掉
- [ ] **Failure Mode 报告** —— 在每个 variant 下找 1-2 个 worst-case ticket，写"为什么这个 variant 在这条上挂掉" → 这是 senior judgment 信号

### 7.4 验收

- [ ] D 在**至少 3 个指标**上显著优于 A / B / C（不显著的指标也要诚实写在 blog 里 —— "这个维度 multi-agent 没收益，trade-off 在这")
- [ ] 简历 bullet 的所有数字（"~60% token reduction" 等）能被这张表 back up
- [ ] **Blog 草稿 2**：*"Multi-agent vs single-agent for customer support: a benchmarked trade-off study"*

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

---

## 时间预算（6-8 周节奏）

| 周 | 重点 Milestone | 关键产出 |
|---|---|---|
| W1 | M1-M2 | 一条端到端 ticket 真跑通 |
| W2 | M3-M4 | 5 SaaS + 4 层 guardrails 真跑 |
| W3 | **M5（Adversarial Eval Harness）** ⭐ | Attribution 表 + Ablation 表 + Blog 1 草稿 |
| W4 | M6-M7 上半 | Hybrid retrieval + benchmark 数据集 |
| W5 | **M7（Architecture Ablation）** ⭐ | 主表 + Blog 2 草稿 |
| W6 | **M8（Demo 视频）** ⭐ | 3 分钟视频 + 简历润色 |
| W7-8 | Buffer + 1 个独家 insight + Blog 发出去 | 进面试 |

> **如果时间紧到只能砍**：M5 + M7 + M8 是不可砍的 ⭐ — 这三个里程碑是 top 30% → top 3% 的全部 leverage。
> **可以砍**：M3 砍到只做 2-3 个 SaaS（Stripe / Zendesk 必留）；M6 hybrid retrieval 简化为只做 dense；M9 直接放掉。
