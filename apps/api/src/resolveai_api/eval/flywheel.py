"""Eval → data flywheel (M14) — production traces become versioned eval cases.

The loop: production tickets → (best-effort) trace sink → **stratified sample** →
**PII scrub** → candidate cases → versioned dataset → **dual-scoring regression
gate** (score a change on *both* the legacy and the freshly-harvested set; a
regression on *either* blocks the release) → **failure clustering** that points at
"what to fix next".

Everything here is pure + deterministic (seeded sampling, regex scrub, hand math)
so the whole flywheel is unit-testable with no LLM, no DB, no network — matching
the rest of the repo's eval harness.

Trace/candidate record schema (one JSON object per ticket):
    {
      "id": str, "query": str, "intent": str|null,
      "outcome": "done"|"blocked"|"awaiting_approval",
      "blocked_layer": str|null, "flags": [str], "escalated": bool,
      "tools": [str], "cost_usd": float, "tokens": int
    }
"""

from __future__ import annotations

import json
import random
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# PII scrubbing (data governance — zero residual PII is a hard gate)
# --------------------------------------------------------------------------- #

# Regex-based scrubber: deterministic + hermetic. Presidio (M4) is the heavier,
# production-grade option; kept out of the offline flywheel path on purpose so
# harvesting is fast and testable. Ordering matters (emails before generic ids).
_PII_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "[EMAIL]"),
    ("card", re.compile(r"\b(?:\d[ -]?){13,16}\b"), "[CARD]"),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN]"),
    ("phone", re.compile(r"\b(?:\+?\d{1,2}[ -]?)?\(?\d{3}\)?[ -]?\d{3}[ -]?\d{4}\b"), "[PHONE]"),
    ("customer_id", re.compile(r"\bcus_[A-Za-z0-9]+\b"), "[CUSTOMER_ID]"),
    ("charge_id", re.compile(r"\bch_[A-Za-z0-9]+\b"), "[CHARGE_ID]"),
)


def scrub_text(text: str) -> str:
    """Redact PII (emails, cards, SSNs, phones, Stripe ids) → placeholder tokens."""
    scrubbed = text
    for _name, pattern, token in _PII_PATTERNS:
        scrubbed = pattern.sub(token, scrubbed)
    return scrubbed


def find_pii(text: str) -> list[str]:
    """Return the PII *types* still present in `text` (empty ⇒ clean)."""
    return sorted({name for name, pattern, _ in _PII_PATTERNS if pattern.search(text)})


def assert_no_pii(
    records: Iterable[Mapping[str, Any]], *, fields: Sequence[str] = ("query",)
) -> list[str]:
    """CI hard gate: return violations for any residual PII in `fields`."""
    violations: list[str] = []
    for record in records:
        for field in fields:
            value = record.get(field)
            if isinstance(value, str):
                found = find_pii(value)
                if found:
                    violations.append(f"{record.get('id', '?')}:{field}: {', '.join(found)}")
    return violations


# --------------------------------------------------------------------------- #
# Stratified sampling (avoid the "only sample the blocked ones" bias)
# --------------------------------------------------------------------------- #


def stratum_key(record: Mapping[str, Any]) -> str:
    """Coarse sampling stratum: intent × outcome (keeps the mix representative)."""
    return f"{record.get('intent') or 'unknown'}|{record.get('outcome') or 'unknown'}"


def stratified_sample(
    records: Sequence[Mapping[str, Any]],
    *,
    per_stratum: int,
    seed: int = 0,
) -> list[Mapping[str, Any]]:
    """Deterministically take up to `per_stratum` records from each stratum.

    Seeded shuffle → reproducible candidate sets across runs (important for a
    regression gate that must not flap).
    """
    buckets: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        buckets[stratum_key(record)].append(record)

    rng = random.Random(seed)
    sampled: list[Mapping[str, Any]] = []
    for key in sorted(buckets):
        items = list(buckets[key])
        rng.shuffle(items)
        sampled.extend(items[:per_stratum])
    return sampled


def to_candidate(record: Mapping[str, Any]) -> dict[str, Any]:
    """Scrubbed, normalized candidate case (safe to persist)."""
    return {
        "id": record.get("id"),
        "query": scrub_text(str(record.get("query", ""))),
        "intent": record.get("intent"),
        "outcome": record.get("outcome"),
        "blocked_layer": record.get("blocked_layer"),
        "flags": list(record.get("flags") or []),
        "escalated": bool(record.get("escalated", False)),
        "tools": list(record.get("tools") or []),
        "cost_usd": float(record.get("cost_usd", 0.0) or 0.0),
        "tokens": int(record.get("tokens", 0) or 0),
        "source": "prod",
    }


# --------------------------------------------------------------------------- #
# Failure clustering ("what should we fix next?")
# --------------------------------------------------------------------------- #


def failure_reason(record: Mapping[str, Any]) -> str | None:
    """Primary reason a ticket is *interesting* to review, or None if clean.

    Priority: guardrail block > escalation > tool error. `None` means a clean
    auto-resolve (not a failure).
    """
    if record.get("outcome") == "blocked":
        return f"blocked:{record.get('blocked_layer') or 'unknown'}"
    if record.get("escalated"):
        return "escalated"
    flags = record.get("flags") or []
    if any(str(f).startswith(("hallucinated", "grounding:hallucinated")) for f in flags):
        return "hallucination"
    if any(str(f).startswith(("tool_error", "failed:")) for f in flags):
        return "tool_error"
    return None


def cluster_failures(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Group failing tickets by (intent, reason); return counts + sample ids desc."""
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for record in records:
        reason = failure_reason(record)
        if reason is None:
            continue
        key = (str(record.get("intent") or "unknown"), reason)
        groups[key].append(str(record.get("id", "?")))
    clusters: list[dict[str, Any]] = [
        {"intent": intent, "reason": reason, "count": len(ids), "sample_ids": ids[:5]}
        for (intent, reason), ids in groups.items()
    ]
    clusters.sort(key=lambda c: (-int(c["count"]), str(c["intent"]), str(c["reason"])))
    return clusters


def render_top_failures_md(clusters: Sequence[Mapping[str, Any]], *, n: int = 10) -> str:
    """Markdown table for `reports/flywheel/top_failures.md`."""
    lines = [
        "# Top failure clusters (M14 flywheel)",
        "",
        "Failing / review-worthy tickets grouped by (intent, reason). Highest-count "
        "clusters first — this is the prioritized \"what to fix next\" list.",
        "",
        "| rank | intent | reason | count | sample ids |",
        "|---|---|---|---|---|",
    ]
    for rank, cluster in enumerate(clusters[:n], start=1):
        sample = ", ".join(cluster.get("sample_ids", []))
        lines.append(
            f"| {rank} | {cluster.get('intent')} | {cluster.get('reason')} "
            f"| {cluster.get('count')} | {sample} |"
        )
    if not clusters:
        lines.append("| — | — | (no failures) | 0 | — |")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Versioned datasets
# --------------------------------------------------------------------------- #


def dataset_manifest(cases: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Provenance for a dataset version: size + distribution by intent/outcome."""
    return {
        "count": len(cases),
        "by_intent": dict(Counter(str(c.get("intent") or "unknown") for c in cases)),
        "by_outcome": dict(Counter(str(c.get("outcome") or "unknown") for c in cases)),
        "by_source": dict(Counter(str(c.get("source") or "unknown") for c in cases)),
        "failure_clusters": cluster_failures(cases),
    }


def write_dataset_version(
    cases: Sequence[Mapping[str, Any]], out_dir: Path
) -> dict[str, Path]:
    """Write `cases.jsonl` + `manifest.json` into a version dir (e.g. data/eval/v2)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cases_path = out_dir / "cases.jsonl"
    with cases_path.open("w", encoding="utf-8") as handle:
        for case in cases:
            handle.write(json.dumps(case, ensure_ascii=False, default=str) + "\n")
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(dataset_manifest(cases), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return {"cases": cases_path, "manifest": manifest_path}


# --------------------------------------------------------------------------- #
# Dual-scoring regression gate
# --------------------------------------------------------------------------- #

DEFAULT_THRESHOLDS: dict[str, float] = {
    "auto_resolve_rate_min_drop": 0.05,  # allow ≤5pp drop
    "guardrail_miss_rate_max_increase": 0.02,  # allow ≤2pp more misses
    "mean_cost_usd_max_increase_pct": 20.0,  # allow ≤20% cost creep
}


def score_dataset(cases: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    """Summarize a labeled dataset's outcomes into gate-able metrics."""
    total = len(cases)
    if total == 0:
        return {"auto_resolve_rate": 0.0, "guardrail_miss_rate": 0.0, "mean_cost_usd": 0.0}
    auto_resolved = sum(
        1 for c in cases if c.get("outcome") == "done" and not c.get("escalated")
    )
    # A "miss" = a case labeled as should-block (expected_block) that was NOT blocked.
    misses = sum(
        1
        for c in cases
        if c.get("expected_block") and c.get("outcome") != "blocked"
    )
    cost = sum(float(c.get("cost_usd", 0.0) or 0.0) for c in cases)
    return {
        "auto_resolve_rate": auto_resolved / total,
        "guardrail_miss_rate": misses / total,
        "mean_cost_usd": cost / total,
    }


def regression_violations(
    current: Mapping[str, float],
    baseline: Mapping[str, float],
    thresholds: Mapping[str, float] | None = None,
) -> list[str]:
    """Return violations where `current` regressed vs `baseline` beyond thresholds."""
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    violations: list[str] = []

    base_resolve = float(baseline.get("auto_resolve_rate", 0.0))
    cur_resolve = float(current.get("auto_resolve_rate", 0.0))
    if base_resolve - cur_resolve > th["auto_resolve_rate_min_drop"]:
        violations.append(
            f"auto_resolve_rate dropped {base_resolve:.3f}→{cur_resolve:.3f} "
            f"(> {th['auto_resolve_rate_min_drop']:.3f})"
        )

    base_miss = float(baseline.get("guardrail_miss_rate", 0.0))
    cur_miss = float(current.get("guardrail_miss_rate", 0.0))
    if cur_miss - base_miss > th["guardrail_miss_rate_max_increase"]:
        violations.append(
            f"guardrail_miss_rate rose {base_miss:.3f}→{cur_miss:.3f} "
            f"(> {th['guardrail_miss_rate_max_increase']:.3f})"
        )

    base_cost = float(baseline.get("mean_cost_usd", 0.0))
    cur_cost = float(current.get("mean_cost_usd", 0.0))
    if base_cost > 0 and (cur_cost - base_cost) / base_cost * 100.0 > th[
        "mean_cost_usd_max_increase_pct"
    ]:
        violations.append(
            f"mean_cost_usd rose {base_cost:.4f}→{cur_cost:.4f} "
            f"(> {th['mean_cost_usd_max_increase_pct']:.0f}%)"
        )
    return violations


def dual_score_gate(
    *,
    results: Mapping[str, Mapping[str, float]],
    baselines: Mapping[str, Mapping[str, float]],
    thresholds: Mapping[str, float] | None = None,
) -> dict[str, list[str]]:
    """Score a change on every dataset; a regression on *any* set is a violation.

    `results`/`baselines` are keyed by dataset name (e.g. "legacy", "harvested").
    Returns {dataset_name: [violations]}.
    """
    return {
        name: regression_violations(results[name], baselines.get(name, {}), thresholds)
        for name in results
    }


def gate_failed(gate_result: Mapping[str, Sequence[str]]) -> bool:
    return any(len(v) > 0 for v in gate_result.values())
