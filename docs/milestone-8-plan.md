# Milestone 8 — Chaos Demo & Video Artifact

**Status:** Implemented (see [roadmap.md](roadmap.md) Milestone 8).

**Goal:** Prove the system scales, wire online regression on top of the M7 eval
primitives, and turn the whole story into a repeatable demo video. Three
deliverables: a 5K-concurrent chaos load harness, OTel spans feeding an EvalGate
push + a regression gate, and an automated Playwright recorder + narration kit.

**Design principle:** library-first and additive. A new `LLM_BACKEND=fake`
isolates framework throughput from model latency; OTel spans and EvalGate are
no-ops unless configured, so the production path is unchanged (121/121 tests
green).

---

## 1. What shipped

| Area | Implementation |
|---|---|
| Fake LLM backend | `core/_fake_llm.py` — `FakeChatModel` + `FakeStructuredRunnable`; deterministic, zero-network. Triage routes by keyword so billing/technical/escalation paths all run. Wired via `LLM_BACKEND=fake` in `core/llm.py` (+ `config.py`) |
| Chaos load | `scripts/chaos_load.py` — `asyncio.Semaphore` fan-out over generated mock tickets through the real `SupervisorGraph`; P50/P95/P99 + throughput; JSON + markdown report; non-zero exit when P95 target missed |
| OTel spans | `observability/tracing.py` `get_tracer()/span()` no-op helper; `ticket.run` + `agent.{node}` + `guardrail.block` spans in `agents/supervisor.py`; `tool.call` span in `core/executor.py` |
| EvalGate | `observability/evalgate.py` — `build_run_summary()` (tokens/cost/tool-error/latency from `RunTrace`) + `EvalGateClient.push()` (httpx, no-op when `EVALGATE_ENDPOINT` unset, swallows network errors) |
| Regression gate | `scripts/regression_gate.py` — scores a benchmark slice under `capture_run()` (reuses M7 judge/pricing/trace), pushes summaries to EvalGate, compares to `reports/baseline/metrics_baseline.json`, exits non-zero on regression (CI-usable) |
| Demo recorder | `apps/web/playwright.config.ts` + `apps/web/demo/record.spec.ts` — drives the 4 beats with on-screen caption overlays, records a real `webm`; resilient when the web app is offline |
| Demo artifacts | `scripts/render_metrics_page.py` — `metrics.html` (chaos P95 gauge + ablation table) and `trace.html` (live guardrail/agent trace incl. the real cross-tenant `PermissionError`) |
| Narration kit | `docs/demo/narration.md` (timestamped voiceover + captions) + `docs/demo/shot-list.md` (run book) |
| Make targets | `chaos`, `regression-gate`, `demo-assets`, `demo-record`, `obs` |
| Tests | `apps/api/tests/test_m8.py` (13 tests) |

---

## 2. Measured results

Chaos load on the fake backend (laptop, `--total 5000 --concurrency 200`):

| Metric | Value |
|---|---:|
| Tickets completed | 5000 / 5000 (0 errors) |
| Throughput | ~1248 req/s |
| Mean latency | 0.15 s |
| P50 latency | 0.15 s |
| **P95 latency** | **0.18 s** (target < 6 s → PASS) |
| P99 latency | 0.19 s |

> The fake backend isolates **framework** concurrency overhead (LangGraph
> orchestration, checkpointer, executor) from model latency. Real end-to-end
> latency on local Ollama is far higher and CPU-bound; run `--backend ollama`
> with a warm server to measure it. The P95 < 6s target is the framework gate.

Regression gate baseline (`reports/baseline/metrics_baseline.json`, fake backend,
variant D, 24 tickets): P95 ≈ 4.6 ms (orchestration only), auto-resolve 100%,
tool-error 0%, ~132 tokens/ticket. Re-running the gate against this baseline
passes; a >50% P95 increase or >5pp auto-resolve drop fails it.

---

## 3. How to run

```bash
make chaos                       # 5K concurrent, writes reports/chaos/
make regression-gate             # gate vs baseline (refresh: --update-baseline)
make demo-assets                 # metrics.html + trace.html
make demo-record                 # record the video (start `make dev` first)
make obs                         # local OTel collector (debug exporter)
```

See [`docs/demo/shot-list.md`](demo/shot-list.md) for the full recording run book.

---

## 4. Honest caveats

- **Demo "recording" is automated, not narrated.** The Playwright recorder
  produces a real captioned `webm`; voiceover (from `narration.md`) is added in
  Loom/QuickTime, or the captioned silent video is shipped as-is.
- **UI stays as-is** (per scope decision). The chat UI shows agent step cards +
  guardrail flag chips + a blocked banner, but not tool calls / a trace
  timeline / tenant switching. The trace-heavy beats (Layer highlighting,
  cross-tenant `PermissionError`, chaos metrics, ablation table) are rendered
  into `trace.html` / `metrics.html` and captured by the same recorder.
- **Cross-tenant block is reproduced at the Layer-4 boundary.** By design,
  per-identity namespacing makes checkpoint keys disjoint, so a public
  `stream()` call can't collide; the demo exercises `IsolatedCheckpointer`'s
  tuple-namespace check directly (the defense of record), which raises
  `CrossTenantAccessBlocked` with the exact namespace-mismatch message.
- **Fake backend numbers are about throughput, not quality.** Token/$ figures
  under fake are modeled placeholders; use Ollama/Anthropic for real economics.
- **EvalGate is push-only + no-op locally.** `EvalGateClient.push()` posts trace
  summaries when `EVALGATE_ENDPOINT` is set; the committed online-regression
  *gate* works offline by comparing to the baseline file.

---

## 5. Forward adaptation

- The `ticket.run` / `agent.{node}` / `tool.call` spans are the hook for a real
  Jaeger/Tempo backend (swap the collector's `debug` exporter) and for
  per-tenant SLO dashboards in M9.
- `build_run_summary()` is the single payload shape for both the online EvalGate
  push and the batch regression gate, so production traces and CI regression
  score identical metrics.
- `LLM_BACKEND=fake` is reusable for any future load/perf test or for fast,
  deterministic e2e CI that exercises orchestration without a model.
