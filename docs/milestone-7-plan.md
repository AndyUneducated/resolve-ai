# Milestone 7 — Quantified Architecture Ablation

**Status:** Implemented (see [roadmap.md](roadmap.md) Milestone 7).

**Goal:** Turn "I used multi-agent / Plan-and-Execute / structured handoff / cost routing" into "I **benchmarked** the cost and benefit of each choice and can defend every trade-off." Ship a reusable eval library + a 120-ticket benchmark + a runner that produces the Architecture Ablation Table across 4 controlled configurations.

**Design principle:** additive + library-first. The four variants are one `VariantSpec` over four independent axes; the production path stays on variant **D** through defaults, so M1–M6 behavior is unchanged (108/108 tests green). Token/tool tracing is a cross-cutting no-op unless an eval run activates it.

---

## 1. The four variants

Expressed as one `VariantSpec` (`eval/variants.py`) toggling four axes; `build_variant()` returns a uniform `VariantRunner`.

| Variant | Topology | Handoff | Business strategy | Triage tier | Validates |
|---|---|---|---|---|---|
| **A** | single agent | — | ReAct | vertical | Is multi-agent worth it at all? |
| **B** | 4 agents | full transcript | Plan-Execute | triage | Value of a *structured* ticket summary |
| **C** | 4 agents | structured | ReAct | triage | Value of Plan-and-Execute over ReAct |
| **D** | 4 agents | structured | Plan-Execute | triage | Shipped baseline |
| **D_triage_vertical** | 4 agents | structured | Plan-Execute | **vertical** | Cost-routing micro-ablation |

Runs invoke the compiled LangGraph **directly (no guardrail wrapper)**, so numbers reflect the agent architecture, not the constant (M5-owned) guardrail layer — notably the vertical-tier policy judge.

---

## 2. What shipped

| Area | Implementation |
|---|---|
| Token/tool trace | `core/usage.py` — contextvar `RunTrace`; `TierUsageCallback` buckets every chat-model call by cost tier (with Ollama `prompt_eval_count`/`eval_count` fallback); `record_tool_call` logs each `Executor` invocation. No-op unless `capture_run()` is active |
| Eval trace surface | `eval/trace.py` — re-exports `core.usage` primitives + `classify_tool_errors` (failed call / wrong-tool / hallucinated entity) |
| Pricing | `eval/pricing.py` — tier→representative-Anthropic price (triage≈Haiku, vertical≈Sonnet); `cost_usd` so cost routing shows up. Tokens real, dollars modeled |
| Resolution judge | `eval/judge.py` — `ResolutionJudge` → structured `ResolutionVerdict(resolved, score, reason)`; runs OUTSIDE the token window so judge tokens never pollute variant counts |
| Variants | `eval/variants.py` — `VariantSpec` + registry, `VariantRunner`, `build_variant`, `_build_single_agent_graph` (1 LLM + all 12 tools, ReAct) |
| Scoring + report | `eval/arch_scoring.py` — per-variant aggregates (tokens, $/ticket, P50/P95, auto-resolve, tool-error), Ablation Table + `Δ (D vs A)`, cost-routing table, failure-mode report; `render_markdown()` |
| Runner | `scripts/eval_architecture.py` — variant×ticket loop → `reports/arch_eval_<ts>.{jsonl,json,md}`; graceful per-ticket timeout/error handling |
| Benchmark | `apps/api/tests/fixtures/benchmark_tickets.jsonl` — 120 hand-authored tickets (72 billing / 36 technical / 12 escalation), ground-truth `expected_intent` / `expected_resolution_path` / `expected_tool_calls` + `rubric`, aligned to MCP mock seed entities |
| Blog draft | `docs/blog/multi-agent-tradeoffs.md` |
| Tests | `apps/api/tests/test_arch_eval.py` (17 tests) |

---

## 3. Key file changes

- New eval package: `apps/api/src/resolveai_api/eval/{__init__,trace,pricing,judge,variants,arch_scoring}.py`
- Cross-cutting accounting: `apps/api/src/resolveai_api/core/usage.py`
- LLM factory hooks tier callback: `apps/api/src/resolveai_api/core/llm.py`
- Executor records tool calls: `apps/api/src/resolveai_api/core/executor.py`
- Additive `GraphOptions` refactor: `apps/api/src/resolveai_api/agents/supervisor.py`
- Billing handoff/strategy + ReAct builder: `apps/api/src/resolveai_api/agents/billing.py`, `agents/billing_graph.py`
- Triage tier override + handoff plumbing: `agents/triage.py`, `agents/technical.py`, `agents/escalation.py`
- Runner: `scripts/eval_architecture.py`
- Benchmark + tests: `apps/api/tests/fixtures/benchmark_tickets.jsonl`, `apps/api/tests/test_arch_eval.py`
- Docs: `docs/blog/multi-agent-tradeoffs.md`, `docs/roadmap.md`

---

## 4. Metrics measured

- **Token / ticket** (input + output split) — real local-Ollama counts captured by cost tier, including nested Plan-Execute sub-graph and structured-output calls that never reach message state.
- **$ / ticket** — modeled: each tier priced at a representative Anthropic list price. Token counts are real; only the USD conversion is a model (keeps the benchmark free + reproducible while showing cost-routing economics).
- **Latency P50 / P95** — end-to-end wall clock per ticket.
- **Auto-resolution rate** — LLM judge against each ticket's rubric.
- **Tool error rate** — failed/blocked tool call + wrong-tool selection (called tool not in `expected_tool_calls`) + hallucinated-entity flags.

---

## 5. How to run

Full ablation (A/B/C/D) + cost-routing variant:

```bash
uv run python scripts/eval_architecture.py --variants A,B,C,D --cost-routing
```

Fast smoke (3 tickets/category, fewer steps), e.g. one variant:

```bash
uv run python scripts/eval_architecture.py --variants D --quick --case-timeout 240
```

Outputs land in `reports/arch_eval_<ts>.{jsonl,json,md}`; paste the `.md` Ablation Table into `docs/blog/multi-agent-tradeoffs.md`.

> Prereqs for grounded technical answers: seed the KB first (`uv run python scripts/seed_db.py --truncate`) so the Technical Agent retrieves real docs instead of degrading to escalation. The runner enables all 5 MCP servers automatically and uses `CHECKPOINT_BACKEND=memory`.

---

## 6. Validation executed in this milestone

Executed:

- `uv run python -m pytest -q` → `108 passed` (full suite; confirms the additive refactor is backward-compatible — production stays on variant D).
- `uv run python -m pytest apps/api/tests/test_arch_eval.py -q` → `17 passed`.
- Live single-ticket smoke (variant D, technical) against Ollama → a complete `outcome=ok` row with **real tokens (1277 in / 80 out by tier)**, modeled cost, latency, `agent_path=["triage","technical"]` (`path_match=true`), captured tool calls (`zendesk.get_ticket_history`), and a scored judge verdict + reason. All three markdown tables rendered.

Coverage highlights:

- Pricing math + cost-routing (triage cheaper than vertical for identical tokens).
- `TierUsageCallback` bucketing + Ollama `response_metadata` fallback + no-op without an active trace.
- `classify_tool_errors` across all three error kinds; clean when all calls expected.
- `arch_scoring` aggregation, percentile interpolation, `Δ (D vs A)`, error-row exclusion.
- `build_variant` smoke for A/B/C/D/D_triage_vertical; `SupervisorGraph` default options == variant D.
- Judge no-LLM paths (blocked / empty answer) and verdict schema bounds.

---

## 7. Honest caveats

- **Headline numbers are TBD pending a full run.** On a local 9B Ollama, a single billing Plan-Execute ticket can exceed 200s, so the A/B/C/D table is intentionally left as `TBD` (matching the blog-1 convention) rather than fabricated. Fill it by running the harness on the target hardware/model and pasting the generated `.md`.
- **Variant C affects billing only.** The Technical Agent is already a fixed 3-phase pipeline (not Plan-Execute), so the ReAct ablation is meaningful for billing; this is called out in the blog rather than hidden.
- **Single-agent (A) has no KB tool.** `kb.search` is in-process (not an MCP tool), so variant A sees the 12 SaaS tools but not KB grounding — a realistic disadvantage of the single-agent design, noted in the failure-mode write-up.
- One pre-existing ruff warning in `core/checkpointer.py` is unrelated to this milestone and left untouched.

---

## 8. Forward adaptation for M8 / M9

- **M8 (chaos/demo + online regression):** the same `RunTrace` (tokens by tier + tool calls), `pricing`, `judge`, and `arch_scoring` primitives feed online regression via EvalGate; the Ablation Table is the artifact shown in the demo video.
- **M9 (multi-tenant):** variants run under namespaced `(tenant, customer, thread)` checkpointing already; the harness is tenant-agnostic and reusable per tenant.
