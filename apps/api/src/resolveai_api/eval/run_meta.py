"""Reproducibility metadata shared by eval scripts and the e2e orchestrator.

Captures *what* ran (command line, effective models, key env, fixture versions,
artifact paths) so a run can be reconstructed without re-executing it. This is
deliberately speed-independent: nothing here measures or tunes latency.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

TRACKED_ENV_KEYS = (
    "LLM_BACKEND",
    "OLLAMA_BASE_URL",
    "TRIAGE_MODEL",
    "VERTICAL_MODEL",
    "LLAMA_GUARD_MODEL",
    "POLICY_JUDGE_MODEL",
    "CHECKPOINT_BACKEND",
    "GUARDRAIL_L1",
    "GUARDRAIL_L2",
    "GUARDRAIL_L3",
    "GUARDRAIL_L4",
    "GUARDRAIL_FAIL_CLOSED",
    "RERANKER_ENABLED",
    "RLS_ENABLED",
    "DATABASE_URL",
    "APP_DATABASE_URL",
)


def redact_dsn(value: str) -> str:
    """Hide the password component of a DSN before persisting it."""
    return re.sub(r"://([^:/@]+):([^@/]+)@", r"://\1:***@", value)


def env_snapshot() -> dict[str, str | None]:
    snap: dict[str, str | None] = {}
    for key in TRACKED_ENV_KEYS:
        value = os.environ.get(key)
        if value is not None and key.endswith("DATABASE_URL"):
            value = redact_dsn(value)
        snap[key] = value
    return snap


def model_snapshot() -> dict[str, Any]:
    """Effective model identifiers — reads settings only, no network call."""
    try:
        from resolveai_api.config import get_settings

        get_settings.cache_clear()
        s = get_settings()
        return {
            "llm_backend": s.llm_backend,
            "triage_model": s.triage_model,
            "vertical_model": s.vertical_model,
            "llama_guard_model": s.llama_guard_model,
            "policy_judge_model": s.policy_judge_model,
            "embedding_model": s.embedding_model,
            "reranker_model": s.reranker_model,
        }
    except Exception as exc:  # pragma: no cover - defensive only
        return {"error": f"{type(exc).__name__}: {exc}"}


def count_lines(path: Path) -> int:
    with path.open(encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def fixture_snapshot(paths: dict[str, Path]) -> dict[str, Any]:
    """Record path + non-empty line count for each input fixture that exists."""
    out: dict[str, Any] = {}
    for name, path in paths.items():
        if path and path.exists():
            out[name] = {"path": str(path), "lines": count_lines(path)}
    return out


def build_run_meta(
    *,
    argv: list[str],
    started_at: str,
    finished_at: str | None,
    fixtures: dict[str, Path] | None = None,
    artifacts: dict[str, str] | None = None,
    is_complete: bool = True,
    incomplete_reason: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "argv": argv,
        "started_at": started_at,
        "finished_at": finished_at,
        "is_complete": is_complete,
        "incomplete_reason": incomplete_reason,
        "env": env_snapshot(),
        "models": model_snapshot(),
        "fixtures": fixture_snapshot(fixtures or {}),
        "artifacts": artifacts or {},
    }
    if extra:
        meta.update(extra)
    return meta
