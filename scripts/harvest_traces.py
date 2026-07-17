"""Harvest production traces → PII-scrubbed, versioned eval candidates (M14).

The production side of the data flywheel. Reads a JSONL of ticket traces (as
written by the app's trace sink when `TRACE_SINK_PATH` is set), stratified-samples
them, scrubs PII, and writes candidate cases + a failure-cluster report. Optionally
promotes the candidates into a versioned dataset (`data/eval/vN/`).

Usage:
    # after running traffic with TRACE_SINK_PATH=data/traces.jsonl
    uv run python scripts/harvest_traces.py --input data/traces.jsonl
    uv run python scripts/harvest_traces.py --input data/traces.jsonl \\
        --per-stratum 20 --dataset-version data/eval/v2

Exit code is non-zero if any residual PII is detected (hard data-governance gate).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "apps" / "api" / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "apps" / "api" / "src"))

from resolveai_api.eval.flywheel import (  # noqa: E402
    assert_no_pii,
    cluster_failures,
    render_top_failures_md,
    stratified_sample,
    to_candidate,
    write_dataset_version,
)

CANDIDATES_DIR = ROOT / "data" / "candidates"
FLYWHEEL_REPORTS = ROOT / "reports" / "flywheel"


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Harvest traces into eval candidates.")
    parser.add_argument("--input", type=Path, required=True, help="traces JSONL")
    parser.add_argument("--per-stratum", type=int, default=20)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--dataset-version", type=Path, default=None,
                        help="also promote candidates into this versioned dir")
    return parser.parse_args()


def run() -> int:
    args = parse_args()
    records = _load_jsonl(args.input)
    sampled = stratified_sample(records, per_stratum=args.per_stratum, seed=args.seed)
    candidates = [to_candidate(r) for r in sampled]

    # Hard data-governance gate: nothing at rest may contain residual PII.
    violations = assert_no_pii(candidates)
    if violations:
        print(f"[harvest] PII GATE FAILED — {len(violations)} residual PII field(s):")
        for v in violations[:20]:
            print(f"  - {v}")
        return 2

    CANDIDATES_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    out = CANDIDATES_DIR / f"candidates_{ts}.jsonl"
    with out.open("w", encoding="utf-8") as handle:
        for case in candidates:
            handle.write(json.dumps(case, ensure_ascii=False) + "\n")

    clusters = cluster_failures(candidates)
    FLYWHEEL_REPORTS.mkdir(parents=True, exist_ok=True)
    top = FLYWHEEL_REPORTS / "top_failures.md"
    top.write_text(render_top_failures_md(clusters), encoding="utf-8")

    print(
        f"[harvest] {len(records)} traces → {len(candidates)} scrubbed candidates "
        f"(PII gate passed) → {out}"
    )
    print(f"[harvest] top failure clusters → {top}")

    if args.dataset_version is not None:
        paths = write_dataset_version(candidates, args.dataset_version)
        print(f"[harvest] promoted dataset version → {paths['cases']} (+ manifest)")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
