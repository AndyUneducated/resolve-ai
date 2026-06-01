"""Shared preflight contract for eval harnesses.

Centralises the "can this run even start?" checks so every harness fails the
same way, with a machine-readable error code, instead of each script growing its
own ad-hoc check that drifts. These checks are speed-independent: they verify
*availability* of dependencies, not their latency.
"""

from __future__ import annotations

from typing import Any

import httpx

from resolveai_api.config import get_settings

# Machine-readable error codes. Surfaced in the message as `[PREFLIGHT:<code>]`
# so logs/automation can branch on the cause without parsing prose.
OLLAMA_UNREACHABLE = "OLLAMA_UNREACHABLE"
MODELS_MISSING = "MODELS_MISSING"
DB_UNREACHABLE = "DB_UNREACHABLE"

# Reusable disclaimer so reports never conflate an engineering degradation
# (e.g. an optional dependency not installed) with a model-capability result.
DEGRADED_NOTE = (
    "> Degraded paths are engineering conditions (missing optional dependency, "
    "disabled feature), **not** model-capability results. Do not read a degraded "
    "row as the model failing."
)


class PreflightError(RuntimeError):
    """Raised when a required dependency is unavailable. Carries a `.code`."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[PREFLIGHT:{code}] {message}")
        self.code = code


def _have(installed: set[str], required: str) -> bool:
    # Prefix-tolerant: a configured tag like "llama-guard3:8b" needs an exact
    # match, while a bare "qwen3.5" is satisfied by any "qwen3.5:<variant>".
    return any(name == required or name.startswith(f"{required}:") for name in installed)


async def check_ollama(
    *, required_models: set[str] | None = None, timeout_s: float = 8.0
) -> dict[str, Any]:
    """Verify Ollama is reachable and required models are installed.

    Returns a status dict (recorded in the run manifest). Raises PreflightError
    with a machine-readable code if the contract is not satisfiable.
    """
    settings = get_settings()
    if settings.llm_backend != "ollama":
        return {"backend": settings.llm_backend, "checked": False}

    url = settings.ollama_base_url.rstrip("/") + "/api/tags"
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            response = await client.get(url)
            response.raise_for_status()
    except Exception as exc:  # pragma: no cover - env dependent
        raise PreflightError(
            OLLAMA_UNREACHABLE,
            f"Ollama is unreachable at {settings.ollama_base_url}. "
            "Start Ollama and pull the required models first.",
        ) from exc

    payload = response.json()
    installed = {
        str(item.get("name", ""))
        for item in payload.get("models", [])
        if isinstance(item, dict)
    }
    required = {m for m in (required_models or set()) if m}
    missing = sorted(m for m in required if not _have(installed, m))
    if missing:  # pragma: no cover - env dependent
        available = ", ".join(sorted(installed)) or "(none)"
        raise PreflightError(
            MODELS_MISSING,
            f"Missing Ollama model(s): {missing}.\n"
            f"  Installed locally: {available}\n"
            "  Pull them (`ollama pull <model>`), or align the configured tags "
            "(LLAMA_GUARD_MODEL / POLICY_JUDGE_MODEL in .env) with what you have.",
        )
    return {
        "backend": "ollama",
        "checked": True,
        "installed": sorted(installed),
        "required": sorted(required),
    }


async def check_database(tenant_id: str) -> dict[str, Any]:
    """Verify the Postgres engine is reachable under the configured (RLS) role."""
    from sqlalchemy import text

    from resolveai_api.core.db import tenant_session
    from resolveai_api.retrieval.store import get_engine

    try:
        engine = get_engine()
        async with tenant_session(engine, tenant_id) as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - env dependent
        raise PreflightError(
            DB_UNREACHABLE,
            "Postgres is unreachable or the RLS role is misconfigured. "
            "Start the DB (`docker compose up -d postgres`), seed it "
            "(`uv run python scripts/seed_db.py`), and check APP_DATABASE_URL.",
        ) from exc
    return {"checked": True, "tenant_id": tenant_id}
