# 面向客户的 AI 为何需要 4 层 Guardrails

_草稿标题：_ **Why customer-facing AI needs 4 layers of guardrails: 200 adversarial prompts, attribution-tested**

## 论点

对能调用 tools 并持久化 state 的 customer support agents，单一「safety model」不够。Guardrails 必须在以下层次组合：

1. **Input layer** — prompt-level intent 与 injection screening。
2. **Execution layer** — tool runtime blast-radius containment。
3. **Output layer** — 用户可见文本前的 policy 与 hallucination checks。
4. **Memory layer** — checkpointed state 上的 tenant/customer isolation。

## 实验设置

- 200 条 adversarial prompts：
  - jailbreak（50）
  - indirect injection（50）
  - pii extraction（30）
  - unauthorized concession（40）
  - cross-tenant（30）
- 50 条 benign support tickets，测 false-positive
- Profile matrix：
  - `baseline`
  - `l1_only`、`l3_only`、`l4_only`
  - `ablate_l1`、`ablate_l3`、`ablate_l4`

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

- Baseline benign blocked：`TBD/TBD`
- Top false-positive flags：
  - `TBD`

## 解读

### 为何 L2 可能不动 leak-rate 表

Layer 2 是 sandboxing。它限制成功 injection 在 runtime 能做什么（filesystem/network/process blast radius），但不本身 classify 用户 prompts 或 redact  outgoing text。因此：

- L2 toggle 在 prompt-level leak metrics 上 little/no change 是预期的，
- 但若 L1/L3 miss，L2 仍 materially 降低 exploit impact。

写文中应 explicit 这一 distinction，避免「表无 delta」被误读为「无价值」。

## 复现步骤

```bash
uv run python scripts/eval_adversarial.py
uv run python scripts/eval_report.py --input reports/eval_<timestamp>.jsonl
```

## Release notes

- 每个 ablation 行包含一个 concrete leaked example。
- 若某 layer 未改善 metrics，仍在文中保留并做 transparent trade-off analysis。
