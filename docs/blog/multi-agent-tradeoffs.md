# Multi-agent vs single-agent for customer support：benchmarked trade-off 研究

_草稿标题：_ **Multi-agent vs single-agent for customer support: a benchmarked trade-off study**

## 论点

「用 multiple agents」和「用 Plan-and-Execute」是 architecture 决策，不是 default。它们各有成本（更多 LLM calls、更多 orchestration）和收益（更便宜的 routing、更少 wrong tool calls、更高 resolution rate）。本研究在固定 benchmark 上测量成本与收益，让每个 trade-off 都能用数字 defend，而非凭感觉。

## 我们变什么

四个 configuration，各自对同一 120-ticket benchmark 运行（`apps/api/tests/fixtures/benchmark_tickets.jsonl`，60% billing / 30% technical / 10% escalation，每条 ticket 带 ground-truth intent、resolution path、expected tool calls 与 resolution rubric）：

| Variant | Topology | Handoff | Business strategy | Triage tier | 验证什么 |
|---|---|---|---|---|---|
| **A** | single agent | — | ReAct | vertical | Multi-agent 是否值得？ |
| **B** | 4 agents | full transcript | Plan-Execute | triage | Handoff 时 *structured* ticket summary 的价值 |
| **C** | 4 agents | structured | ReAct | triage | Plan-and-Execute 相对 single-step ReAct 的价值 |
| **D** | 4 agents | structured | Plan-Execute | triage | 已 ship 的配置（baseline） |

另加 cost-routing micro-ablation：**D**（triage 用 cheap tier）vs **D_triage_vertical**（triage 强制 expensive vertical tier）。

## 如何测量

- **Tokens** 为真实值，经 contextvar trace（`core/usage.py`）从本地 Ollama runs 捕获，按 cost tier 分桶每次 chat-model call — 含 nested Plan-Execute sub-graph 与 structured-output calls。
- **$/ticket** 为 *modeled*：每个 cost tier 按 representative Anthropic list price 计价（triage ≈ Haiku，vertical ≈ Sonnet）。Token counts 真实；仅 dollar conversion 是 model，benchmark 免费可复现，仍能展示 cost routing economics。
- **Latency** 为每条 ticket 端到端 wall clock（P50 / P95）。
- **Auto-resolution** 由 LLM judge（`eval/judge.py`）对照每条 ticket 的 rubric；judge 在 token-capture window **外**运行，不污染 variant 的 token/cost 数字。
- **Tool error** = 任意 failed/blocked tool call、wrong-tool selection（调用了不在 ticket expected set 中的 tool）、或 hallucinated entity flag。
- Runs **直接**调用 compiled LangGraph，**无 guardrail wrapper**，数字反映 agent architecture 而非恒定（M5 负责）的 guardrail layer — notably vertical-tier policy judge。

复现：

```bash
uv run python scripts/eval_architecture.py --variants A,B,C,D --cost-routing
```

## Architecture Ablation Table

| Variant | Token/ticket | $/ticket | P95 (s) | Auto-resolve | Tool error |
|---|---:|---:|---:|---:|---:|
| A · Single-Agent | TBD | TBD | TBD | TBD | TBD |
| B · 4-Agent + full transcript handoff | TBD | TBD | TBD | TBD | TBD |
| C · 4-Agent + ReAct | TBD | TBD | TBD | TBD | TBD |
| **D · Final** | TBD | TBD | TBD | TBD | TBD |
| **Δ (D vs A)** | TBD | TBD | TBD | TBD | TBD |

_（由 `scripts/eval_architecture.py` 生成；全量 run 后将 `arch_eval_<ts>.md` 表粘贴于此。）_

## Cost-routing ablation

| Config | Triage tier | $/ticket | Auto-resolve |
|---|---|---:|---:|
| D | triage (Haiku-priced) | TBD | TBD |
| D_triage_vertical | vertical (Sonnet-priced) | TBD | TBD |

Token counts 相同（同一 local model），故隔离「triage 路由到更便宜 model」的 dollar 影响 — 并确认 cheap classifier 下 auto-resolution 是否保持。

## Failure-mode report

每个 variant 保留 1–2 个 worst cases（judge 分最低 / errors），并写清 *为何* 该 configuration 在该 ticket 上失败。预期可诚实讨论的模式：

- **A（single agent）**：在完整 12-tool surface 上 wrong-tool selection（如对 technical ticket 误用 Stripe）；缺少 billing-specific guardrail framing 时的 over-eager refunds。
- **B（full transcript）**：长 ticket 上 token blow-up；planner 被无关对话 history 分散，相对 compact structured summary。
- **C（ReAct）**：Plan-and-Execute 会排序的步骤缺失（如 refund 前未 verify charge），或 step budget 耗尽。
- **D**：仍输的地方 — 如需要 KB grounding 而 single agent 可即兴的 ticket，或纯 policy 问题上额外 hop 只增 latency 无 resolution 收益。

## 诚实性章节

Multi-agent **无**收益的维度也要报告，不隐藏。例如 B 的 structured-handoff token  savings 在短 ticket 上很小，或 D 因 triage hop 比 A latency 更差 — 那就是 trade-off；点明它是本研究要产出的 senior signal。
