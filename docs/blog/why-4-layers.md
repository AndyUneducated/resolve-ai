# Why customer-facing AI needs 4 layers of guardrails

_Draft title:_ **Why customer-facing AI needs 4 layers of guardrails: 200 adversarial prompts, attribution-tested**

## Thesis

A single "safety model" is not enough for customer support agents that can call tools and persist state. Guardrails must compose across:

1. **Input layer** for prompt-level intent and injection screening.
2. **Execution layer** for tool runtime blast-radius containment.
3. **Output layer** for policy and hallucination checks before user-visible text.
4. **Memory layer** for tenant/customer isolation over checkpointed state.

## Setup

- 200 adversarial prompts:
  - jailbreak (50)
  - indirect injection (50)
  - pii extraction (30)
  - unauthorized concession (40)
  - cross-tenant (30)
- 50 benign support tickets for false-positive measurement
- profile matrix:
  - `baseline`
  - `l1_only`, `l3_only`, `l4_only`
  - `ablate_l1`, `ablate_l3`, `ablate_l4`

## Layer Attribution Table

| Attack category | Layer 1 catch | Layer 2 catch | Layer 3 catch | Layer 4 catch | Miss |
|---|---:|---:|---:|---:|---:|
| jailbreak | TBD | — | TBD | — | TBD |
| indirect_injection | TBD | — | TBD | — | TBD |
| pii_extraction | TBD | — | TBD | — | TBD |
| unauthorized_concession | TBD | — | TBD | — | TBD |
| cross_tenant | — | — | — | TBD | TBD |

## Ablation Table

| Config | Block rate | False positive | Worst-case leaked example |
|---|---:|---:|---|
| baseline | TBD | TBD | — |
| ablate_l1 | TBD | TBD | TBD |
| ablate_l3 | TBD | TBD | TBD |
| ablate_l4 | TBD | TBD | TBD |

## False Positive Analysis

- Baseline benign blocked: `TBD/TBD`
- Top false-positive flags:
  - `TBD`

## Interpretation

### Why L2 may not move leak-rate tables

Layer 2 is sandboxing. It limits what a successful injection can do at runtime (filesystem/network/process blast radius), but it does not itself classify user prompts or redact outgoing text. Therefore:

- it is expected that L2 toggles may show little/no change in prompt-level leak metrics,
- but L2 still materially reduces exploit impact if L1/L3 miss.

This distinction should be explicit in the write-up so "no table delta" is not misread as "no value."

## Repro steps

```bash
uv run python scripts/eval_adversarial.py
uv run python scripts/eval_report.py --input reports/eval_<timestamp>.jsonl
```

## Release notes

- Include one concrete leaked example per ablation row.
- If any layer does not improve metrics, keep it in the post with transparent trade-off analysis.
