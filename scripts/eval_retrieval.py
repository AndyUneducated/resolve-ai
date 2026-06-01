"""检索质量评测 — 在 golden set 上算 Recall@k / MRR@k，并支持 profile 对比。

为 M7 architecture ablation 预埋：同一 runner 换 RETRIEVAL_PROFILE 即可对比
hybrid vs dense_only 的检索命中率（与 M5 的 guardrail_profiles 评测范式一致）。

用法（需 Postgres 已 seed + embedding 可用）:
    uv run python scripts/seed_db.py
    uv run python scripts/eval_retrieval.py
    uv run python scripts/eval_retrieval.py --profiles hybrid,dense_only --k 5
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "apps" / "api" / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "apps" / "api" / "src"))

from resolveai_api.config import get_settings  # noqa: E402
from resolveai_api.core.db import tenant_session  # noqa: E402
from resolveai_api.eval.preflight import DEGRADED_NOTE, check_database  # noqa: E402
from resolveai_api.eval.run_meta import build_run_meta  # noqa: E402
from resolveai_api.retrieval.hybrid import HybridRetriever  # noqa: E402
from resolveai_api.retrieval.metrics import (  # noqa: E402
    aggregate,
    mrr_at_k,
    proportional_recall_at_k,
    recall_at_k,
)
from resolveai_api.retrieval.store import get_engine  # noqa: E402
from sqlalchemy import text  # noqa: E402

DEFAULT_GOLDEN = ROOT / "apps" / "api" / "tests" / "fixtures" / "kb_retrieval_golden.jsonl"
REPORTS_DIR = ROOT / "reports"


def _load_golden(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


async def _resolve_title_ids(tenant_id: str, titles: list[str]) -> dict[str, int]:
    if not titles:
        return {}
    engine = get_engine()
    async with tenant_session(engine, tenant_id) as conn:
        result = await conn.execute(
            text(
                "SELECT id, title FROM kb_documents "
                "WHERE tenant_id = :t AND title = ANY(:titles)"
            ),
            {"t": tenant_id, "titles": titles},
        )
        return {str(row._mapping["title"]): int(row._mapping["id"]) for row in result.fetchall()}


async def eval_profile(
    *, profile: str, golden: list[dict[str, Any]], tenant_id: str, k: int
) -> dict[str, Any]:
    get_settings.cache_clear()
    retriever = HybridRetriever(profile=profile)
    reranker_status = retriever.reranker_status
    per_case: list[dict[str, float]] = []
    details: list[dict[str, Any]] = []

    for row in golden:
        expected_titles = list(row.get("expected_titles") or [])
        title_to_id = await _resolve_title_ids(tenant_id, expected_titles)
        expected_ids = list(title_to_id.values())
        missing = [t for t in expected_titles if t not in title_to_id]

        docs = await retriever.search(query=str(row["query"]), tenant_id=tenant_id, k=k)
        retrieved_ids = [d.id for d in docs]

        case_metrics = {
            f"recall@{k}": recall_at_k(retrieved_ids, expected_ids, k=k),
            f"prop_recall@{k}": proportional_recall_at_k(retrieved_ids, expected_ids, k=k),
            f"mrr@{k}": mrr_at_k(retrieved_ids, expected_ids, k=k),
        }
        per_case.append(case_metrics)
        details.append(
            {
                "id": row.get("id"),
                "query": row.get("query"),
                "expected_ids": expected_ids,
                "missing_titles": missing,
                "retrieved_ids": retrieved_ids,
                **case_metrics,
            }
        )

    return {
        "profile": profile,
        "reranker_status": reranker_status,
        "aggregate": aggregate(per_case),
        "cases": details,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate KB retrieval quality.")
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--profiles", default="hybrid,dense_only")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--tenant", default=None)
    return parser.parse_args()


def _ts() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


async def run() -> int:
    started_at = _ts()
    args = parse_args()
    tenant_id = args.tenant or get_settings().default_tenant_id
    golden = _load_golden(args.golden)
    profiles = [p.strip() for p in args.profiles.split(",") if p.strip()]

    preflight = await check_database(tenant_id)

    results = []
    degraded_paths: list[dict[str, str]] = []
    for profile in profiles:
        result = await eval_profile(
            profile=profile, golden=golden, tenant_id=tenant_id, k=args.k
        )
        results.append(result)
        agg = result["aggregate"]
        status = result["reranker_status"]
        if status == "fallback(rrf)":
            degraded_paths.append(
                {
                    "profile": profile,
                    "component": "reranker",
                    "status": status,
                    "remedy": "uv sync --extra rerank",
                }
            )
            print(
                "[retrieval-eval] WARNING reranker fell back to RRF order "
                "(cross-encoder not loaded). Install with `uv sync --extra rerank` "
                "to evaluate the real rerank path."
            )
        print(
            f"[retrieval-eval] profile={profile} reranker={status} "
            + " ".join(f"{key}={value:.3f}" for key, value in agg.items())
        )

    if degraded_paths:
        print(f"[retrieval-eval] degraded paths: {len(degraded_paths)} (see report)")
        print(f"[retrieval-eval] {DEGRADED_NOTE.lstrip('> ')}")

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    out = REPORTS_DIR / f"retrieval_eval_{ts}.json"
    report = {
        "degraded_paths": degraded_paths,
        "degraded_note": DEGRADED_NOTE,
        "results": results,
    }
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    manifest_path = out.with_suffix(".manifest.json")
    meta = build_run_meta(
        argv=sys.argv[1:],
        started_at=started_at,
        finished_at=_ts(),
        fixtures={"kb_retrieval_golden": args.golden},
        artifacts={"report_json": str(out)},
        extra={
            "harness": "eval_retrieval",
            "profiles": profiles,
            "k": args.k,
            "tenant_id": tenant_id,
            "preflight": preflight,
            "degraded_paths": degraded_paths,
        },
    )
    manifest_path.write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"[retrieval-eval] report → {out}")
    print(f"[retrieval-eval] run manifest → {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
