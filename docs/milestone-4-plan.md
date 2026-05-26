# Milestone 4 — Four-layer guardrails implementation

**Status:** Implemented (see [roadmap.md](roadmap.md) Milestone 4).

**Goal:** Upgrade the existing guardrail hook points from stubs to production-like behavior across input, execution, output, and memory layers while keeping the architecture simple and ablation-friendly for Milestone 5.

---

## 1. What shipped

| Layer | Implementation |
|------|-----------------|
| Input | `InputGuardrail` now runs indirect-injection heuristics, local Ollama Llama Guard classification, and Presidio redaction with timeout/fallback flags |
| Exec | MCP stdio commands are sandbox-wrapped in `docker run`; `SANDBOX_MODE=gvisor` appends `--runtime=runsc`, with `off` for local tests |
| Output | `OutputGuardrail` now does Presidio re-scan, structured policy judge (`PolicyVerdict`), and hallucinated entity checks against `tool_calls` |
| Memory | `IsolatedCheckpointer` enforces `(tenant_id, customer_id)` namespace checks on checkpoint reads/writes and supervisor config propagation |

---

## 2. Key file changes

- Input guardrails: [`apps/api/src/resolveai_api/guardrails/input_filter.py`](../apps/api/src/resolveai_api/guardrails/input_filter.py)
- Exec sandboxing: [`apps/api/src/resolveai_api/mcp/loader.py`](../apps/api/src/resolveai_api/mcp/loader.py), [`apps/api/src/resolveai_api/guardrails/exec_sandbox.py`](../apps/api/src/resolveai_api/guardrails/exec_sandbox.py), [`packages/mcp-servers/Dockerfile`](../packages/mcp-servers/Dockerfile)
- Output guardrails: [`apps/api/src/resolveai_api/guardrails/output_filter.py`](../apps/api/src/resolveai_api/guardrails/output_filter.py), [`apps/api/src/resolveai_api/agents/supervisor.py`](../apps/api/src/resolveai_api/agents/supervisor.py)
- Memory isolation: [`apps/api/src/resolveai_api/core/checkpointer.py`](../apps/api/src/resolveai_api/core/checkpointer.py), [`apps/api/src/resolveai_api/guardrails/memory_isolator.py`](../apps/api/src/resolveai_api/guardrails/memory_isolator.py)
- Config surface: [`apps/api/src/resolveai_api/config.py`](../apps/api/src/resolveai_api/config.py), [`.env.example`](../.env.example)
- Frontend blocked event: [`apps/web/app/chat/page.tsx`](../apps/web/app/chat/page.tsx)

---

## 3. Configuration knobs added

- `SANDBOX_MODE=off|docker|gvisor`
- `MCP_SANDBOX_IMAGE`
- `LLAMA_GUARD_MODEL`, `LLAMA_GUARD_TIMEOUT_MS`
- `PRESIDIO_LANGUAGE`
- `POLICY_JUDGE_MODEL`, `POLICY_JUDGE_TIMEOUT_MS`
- `GUARDRAIL_L1`, `GUARDRAIL_L2`, `GUARDRAIL_L3`, `GUARDRAIL_L4`

All guardrail toggles default to `on`; tests force selected layers off for deterministic runs.

---

## 4. Validation

Executed:

- `uv run pytest apps/api/tests/test_guardrails.py apps/api/tests/test_isolated_checkpointer.py`

Result:

- `11 passed`

Coverage highlights:

- L1 block + Presidio redaction
- L2 sandbox command wrapping for `off` / `docker` / `gvisor`
- L3 policy flags + hallucinated entity detection
- L4 cross-tenant checkpoint access raising `PermissionError`

---

## 5. Notes for next milestones

- The guardrail layer switches (`GUARDRAIL_L1..L4`) are ready for M5 ablation runs.
- `SANDBOX_MODE=off` remains the default for local macOS dev and CI; Linux hosts can turn on `gvisor`.
