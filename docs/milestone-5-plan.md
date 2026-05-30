# Milestone 5 — Adversarial Eval Harness

**Status:** Implemented (see `docs/roadmap.md` Milestone 5).

**Goal:** Move from "we built 4 guardrail layers" to "we can attribute, ablate, and quantify each layer with a reproducible dataset + runner + report pipeline."

---

## 1. What shipped

| Area | Implementation |
|---|---|
| Guardrail semantics spine | Added `guardrails/attribution.py` (`Layer`, `GuardrailConfig`, profile matrix, blocking rules, `GuardrailReport`) as single source of truth |
| Real L2/L4 ablation | `GUARDRAIL_L2` now controls sandbox wrapping; `GUARDRAIL_L4` now controls namespace enforcement in `IsolatedCheckpointer` |
| L3 enforcement | `SupervisorGraph.stream()` now blocks and halts when output flags hit blocking policy (instead of only tagging) |
| Structured eval capture | `SupervisorGraph.stream(..., report_sink=...)` emits machine-readable `GuardrailReport` per run |
| Dataset | `apps/api/tests/fixtures/red_team.jsonl` (200 adversarial) + `apps/api/tests/fixtures/benign_tickets.jsonl` (50 benign) |
| Runner | `scripts/eval_adversarial.py` executes profile matrix, handles cross-tenant seed/attack flow, writes raw JSONL + summary artifacts |
| Scoring/report | `guardrails/eval_scoring.py` + `scripts/eval_report.py` generate Layer Attribution / Ablation / FP analysis in JSON + Markdown |
| Test coverage | Added `apps/api/tests/test_eval_harness.py` for scorer math and L4 ablation behavior |

---

## 2. Key file changes

- Guardrail attribution core: `apps/api/src/resolveai_api/guardrails/attribution.py`
- L2 sandbox toggle wiring: `apps/api/src/resolveai_api/mcp/loader.py`
- L4 toggle + typed exception: `apps/api/src/resolveai_api/core/checkpointer.py`
- L3 blocking + report sink: `apps/api/src/resolveai_api/agents/supervisor.py`
- Output guard tuning: `apps/api/src/resolveai_api/guardrails/output_filter.py`
- Eval scoring: `apps/api/src/resolveai_api/guardrails/eval_scoring.py`
- Eval runner: `scripts/eval_adversarial.py`
- Report renderer: `scripts/eval_report.py`
- Dataset fixtures: `apps/api/tests/fixtures/red_team.jsonl`, `apps/api/tests/fixtures/benign_tickets.jsonl`
- Harness tests: `apps/api/tests/test_eval_harness.py`

---

## 3. Runtime knobs used by M5

- Layer toggles: `GUARDRAIL_L1`, `GUARDRAIL_L2`, `GUARDRAIL_L3`, `GUARDRAIL_L4`
- Sandbox mode: `SANDBOX_MODE=off|docker|gvisor`
- Guard models: `LLAMA_GUARD_MODEL`, `POLICY_JUDGE_MODEL`
- Timeouts: `LLAMA_GUARD_TIMEOUT_MS`, `POLICY_JUDGE_TIMEOUT_MS`
- PII language: `PRESIDIO_LANGUAGE`

Named eval profiles are defined in code (`baseline`, `l1_only`, `l3_only`, `l4_only`, `ablate_l1`, `ablate_l3`, `ablate_l4`, `all_off`).

---

## 4. How to run

Run full eval:

```bash
uv run python scripts/eval_adversarial.py
```

Run a quick smoke:

```bash
uv run python scripts/eval_adversarial.py --quick --configs baseline,ablate_l1
```

Rebuild summary tables from raw JSONL:

```bash
uv run python scripts/eval_report.py --input reports/eval_<timestamp>.jsonl
```

---

## 5. Validation executed in this milestone

Executed:

- `uv run pytest apps/api/tests/test_guardrails.py apps/api/tests/test_isolated_checkpointer.py apps/api/tests/test_eval_harness.py -q`

Result:

- `14 passed`

Coverage highlights:

- L2 sandbox wrapping obeys `GUARDRAIL_L2`
- L4 enabled/disabled behavior changes cross-tenant read outcomes
- Layer-attribution and ablation summary math is deterministic on mocked rows

---

## 6. Notes

- Layer 2 (sandbox) is a blast-radius containment layer; prompt-level leak metrics may remain unchanged when toggled. This is expected and should be called out explicitly in the report/blog.
- The runner performs startup prereq checks for Ollama model availability and Presidio initialization before launching a long eval.
