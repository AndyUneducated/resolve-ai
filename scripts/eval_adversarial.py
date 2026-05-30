"""Run M5 adversarial evals across guardrail ablation profiles."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from collections.abc import Callable
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from resolveai_api.agents.supervisor import SupervisorGraph
from resolveai_api.config import get_settings
from resolveai_api.core.checkpointer import lifespan_checkpointer
from resolveai_api.guardrails.attribution import (
    GuardrailConfig,
    GuardrailReport,
    guardrail_profiles,
)
from resolveai_api.guardrails.eval_scoring import (
    ADVERSARIAL_CATEGORIES,
    build_summary,
    layer_to_str,
    load_jsonl,
    render_markdown,
)
from resolveai_api.guardrails.presidio import get_presidio
from resolveai_api.mcp.toolbelt import ToolBelt

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RED_TEAM = ROOT / "apps" / "api" / "tests" / "fixtures" / "red_team.jsonl"
DEFAULT_BENIGN = ROOT / "apps" / "api" / "tests" / "fixtures" / "benign_tickets.jsonl"
REPORTS_DIR = ROOT / "reports"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run adversarial guardrail eval harness.")
    parser.add_argument("--red-team", type=Path, default=DEFAULT_RED_TEAM)
    parser.add_argument("--benign", type=Path, default=DEFAULT_BENIGN)
    parser.add_argument(
        "--configs",
        type=str,
        default="baseline,l1_only,l3_only,l4_only,ablate_l1,ablate_l3,ablate_l4",
        help="Comma-separated guardrail profile names.",
    )
    parser.add_argument(
        "--categories",
        type=str,
        default="all",
        help="Comma-separated categories, or 'all'.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional global row cap after filtering (0 means no cap).",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Run only 5 samples per category for fast smoke checks.",
    )
    parser.add_argument(
        "--case-timeout",
        type=float,
        default=90.0,
        help="Per-case wall-clock timeout in seconds; exceeding it records a timeout row.",
    )
    return parser.parse_args()


def _parse_configs(configs: str) -> list[str]:
    available = guardrail_profiles()
    names = [name.strip() for name in configs.split(",") if name.strip()]
    if not names:
        raise ValueError("No --configs selected.")
    unknown = [name for name in names if name not in available]
    if unknown:
        raise ValueError(f"Unknown profile(s): {unknown}. Available: {sorted(available)}")
    return names


def _parse_categories(raw: str) -> set[str]:
    if raw.strip().lower() == "all":
        return {*ADVERSARIAL_CATEGORIES, "benign"}
    return {category.strip() for category in raw.split(",") if category.strip()}


def _select_cases(
    rows: list[dict[str, Any]], *, categories: set[str], quick: bool, limit: int
) -> list[dict[str, Any]]:
    filtered = [row for row in rows if row.get("category") in categories]
    if quick:
        by_category: dict[str, list[dict[str, Any]]] = {}
        for row in filtered:
            by_category.setdefault(str(row.get("category")), []).append(row)
        sliced: list[dict[str, Any]] = []
        for category in sorted(by_category):
            sliced.extend(by_category[category][:5])
        filtered = sliced
    if limit > 0:
        return filtered[:limit]
    return filtered


def _apply_profile(profile: GuardrailConfig) -> None:
    os.environ.setdefault("CHECKPOINT_BACKEND", "memory")
    for key, value in profile.as_env().items():
        os.environ[key] = value
    get_settings.cache_clear()


async def _check_prereqs() -> None:
    settings = get_settings()
    get_presidio()
    if settings.llm_backend != "ollama":
        return
    required_models = {settings.llama_guard_model, settings.policy_judge_model}
    url = settings.ollama_base_url.rstrip("/") + "/api/tags"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(url)
            response.raise_for_status()
    except Exception as exc:  # pragma: no cover - env dependent
        raise RuntimeError(
            f"Ollama is unreachable at {settings.ollama_base_url}. "
            "Start Ollama and pull required models first."
        ) from exc
    payload = response.json()
    models = {
        str(item.get("name", ""))
        for item in payload.get("models", [])
        if isinstance(item, dict)
    }
    missing = [model for model in required_models if model not in models]
    if missing:  # pragma: no cover - env dependent
        raise RuntimeError(
            f"Missing Ollama model(s): {missing}. Run `ollama pull <model>` before eval."
        )


def _extract_blocked(events: list[dict[str, str]]) -> bool:
    return any(event.get("type") == "blocked" for event in events)


def _extract_token_usage(state: Any) -> dict[str, int]:
    if state is None:
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    values = getattr(state, "values", {}) or {}
    messages = values.get("messages", []) if isinstance(values, dict) else []
    totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    for message in messages:
        usage = getattr(message, "usage_metadata", None)
        if isinstance(usage, dict):
            totals["input_tokens"] += int(usage.get("input_tokens", 0) or 0)
            totals["output_tokens"] += int(usage.get("output_tokens", 0) or 0)
            totals["total_tokens"] += int(usage.get("total_tokens", 0) or 0)
    if totals["total_tokens"] <= 0:
        totals["total_tokens"] = totals["input_tokens"] + totals["output_tokens"]
    return totals


async def _collect_stream(
    supervisor: SupervisorGraph,
    *,
    message: str,
    customer_id: str,
    tenant_id: str,
    thread_id: str,
) -> tuple[list[dict[str, str]], GuardrailReport, dict[str, int]]:
    events: list[dict[str, str]] = []
    reports: list[GuardrailReport] = []
    async for event in supervisor.stream(
        message=message,
        customer_id=customer_id,
        tenant_id=tenant_id,
        thread_id=thread_id,
        report_sink=reports,
    ):
        events.append(event)
    namespace = f"{tenant_id}::{customer_id}::{thread_id or 'default'}"
    state = await supervisor.graph.aget_state({"configurable": {"thread_id": namespace}})
    usage = _extract_token_usage(state)
    report = reports[-1] if reports else GuardrailReport.from_flags([])
    return events, report, usage


async def _run_case_once(
    supervisor: SupervisorGraph, *, case: dict[str, Any], case_id: str, category: str
) -> tuple[list[dict[str, str]], GuardrailReport, dict[str, int]]:
    if category == "cross_tenant":
        thread_id = str(case["thread"])
        # Seed checkpoint under the source identity first.
        await _collect_stream(
            supervisor,
            message=f"seed checkpoint for {case_id}",
            customer_id=str(case["seed_customer"]),
            tenant_id=str(case["seed_tenant"]),
            thread_id=thread_id,
        )
        return await _collect_stream(
            supervisor,
            message=str(case["prompt"]),
            customer_id=str(case["attack_customer"]),
            tenant_id=str(case["attack_tenant"]),
            thread_id=thread_id,
        )
    return await _collect_stream(
        supervisor,
        message=str(case["prompt"]),
        customer_id=f"eval::{case_id}",
        tenant_id="eval",
        thread_id=case_id,
    )


async def _eval_case(
    supervisor: SupervisorGraph,
    *,
    profile_name: str,
    case: dict[str, Any],
    case_timeout_s: float,
    max_attempts: int = 2,
) -> dict[str, Any]:
    case_id = str(case.get("id"))
    category = str(case.get("category"))
    expected_layer = str(case.get("expected_block_layer", "none"))
    start = time.perf_counter()

    last_error: Exception | None = None
    outcome_on_error = "error"
    events: list[dict[str, str]] = []
    report = GuardrailReport.from_flags([])
    usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    for attempt in range(1, max_attempts + 1):
        try:
            events, report, usage = await asyncio.wait_for(
                _run_case_once(
                    supervisor, case=case, case_id=case_id, category=category
                ),
                timeout=case_timeout_s,
            )
            last_error = None
            break
        except TimeoutError as exc:
            # A hung model call should not retry (it just doubles wall time) or
            # stall the whole run; record it and move on.
            last_error = exc
            outcome_on_error = "timeout"
            break
        except Exception as exc:  # transient ollama / runtime failures
            last_error = exc
            outcome_on_error = "error"
            if attempt < max_attempts:
                await asyncio.sleep(2.0 * attempt)

    if last_error is not None:
        detail = (
            f"case exceeded {case_timeout_s:.0f}s"
            if outcome_on_error == "timeout"
            else f"{type(last_error).__name__}: {last_error}"
        )
        return {
            "id": case_id,
            "category": category,
            "profile": profile_name,
            "prompt": case.get("prompt"),
            "expected_block_layer": expected_layer,
            "expected_intent": case.get("expected_intent"),
            "notes": case.get("notes"),
            "blocked": False,
            "blocking_layer": None,
            "blocking_flag": None,
            "flags": [],
            "outcome": outcome_on_error,
            "leaked": None,
            "attribution_correct": None,
            "latency_ms": round((time.perf_counter() - start) * 1000.0, 2),
            "token_usage": usage,
            "event_types": [event.get("type") for event in events],
            "error": detail,
        }

    latency_ms = (time.perf_counter() - start) * 1000.0
    blocked = _extract_blocked(events) or report.blocked
    actual_layer = layer_to_str(report.blocking_layer)
    leaked = expected_layer != "none" and not blocked
    attribution_correct = False
    if expected_layer == "none":
        attribution_correct = not blocked
    elif blocked and actual_layer is not None:
        attribution_correct = actual_layer == expected_layer

    return {
        "id": case_id,
        "category": category,
        "profile": profile_name,
        "prompt": case.get("prompt"),
        "expected_block_layer": expected_layer,
        "expected_intent": case.get("expected_intent"),
        "notes": case.get("notes"),
        "blocked": blocked,
        "blocking_layer": actual_layer,
        "blocking_flag": report.blocking_flag,
        "flags": report.flags,
        "outcome": report.outcome.value,
        "leaked": leaked,
        "attribution_correct": attribution_correct,
        "latency_ms": round(latency_ms, 2),
        "token_usage": usage,
        "event_types": [event.get("type") for event in events],
        "error": None,
    }


async def _run_profile(
    profile_name: str,
    profile: GuardrailConfig,
    cases: list[dict[str, Any]],
    *,
    case_timeout_s: float,
    on_row: Callable[[dict[str, Any]], None],
) -> list[dict[str, Any]]:
    _apply_profile(profile)
    await _check_prereqs()
    async with AsyncExitStack() as stack:
        checkpointer = await stack.enter_async_context(lifespan_checkpointer())
        toolbelt = await ToolBelt.from_settings()
        supervisor = SupervisorGraph(checkpointer=checkpointer, toolbelt=toolbelt)
        rows: list[dict[str, Any]] = []
        for case in cases:
            row = await _eval_case(
                supervisor,
                profile_name=profile_name,
                case=case,
                case_timeout_s=case_timeout_s,
            )
            on_row(row)
            rows.append(row)
        return rows


def _report_paths() -> tuple[Path, Path, Path]:
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    jsonl_path = REPORTS_DIR / f"eval_{ts}.jsonl"
    json_path = REPORTS_DIR / f"eval_{ts}.json"
    md_path = REPORTS_DIR / f"eval_{ts}.md"
    return jsonl_path, json_path, md_path


async def run() -> int:
    args = parse_args()
    configs = _parse_configs(args.configs)
    categories = _parse_categories(args.categories)

    adversarial = load_jsonl(args.red_team)
    benign = load_jsonl(args.benign)
    cases = _select_cases(adversarial + benign, categories=categories, quick=args.quick, limit=args.limit)
    if not cases:
        raise RuntimeError("No cases selected after applying --categories/--limit.")

    jsonl_path, json_path, md_path = _report_paths()
    all_rows: list[dict[str, Any]] = []
    profiles = guardrail_profiles()

    with jsonl_path.open("w", encoding="utf-8") as handle:
        def _write_row(row: dict[str, Any]) -> None:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()

        for profile_name in configs:
            rows = await _run_profile(
                profile_name,
                profiles[profile_name],
                cases,
                case_timeout_s=args.case_timeout,
                on_row=_write_row,
            )
            all_rows.extend(rows)
            errors = sum(1 for row in rows if row.get("outcome") == "error")
            timeouts = sum(1 for row in rows if row.get("outcome") == "timeout")
            print(
                f"[eval] profile={profile_name} completed rows={len(rows)} "
                f"errors={errors} timeouts={timeouts}"
            )

    summary = build_summary(all_rows)
    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, ensure_ascii=False)
    with md_path.open("w", encoding="utf-8") as handle:
        handle.write(render_markdown(summary))

    print(f"[eval] raw rows: {jsonl_path}")
    print(f"[eval] summary json: {json_path}")
    print(f"[eval] summary md: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
