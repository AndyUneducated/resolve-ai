"""Pytest fixtures — keep tests hermetic (no Postgres, no real LLM)."""

from __future__ import annotations

import os

# Force in-memory checkpointer & ollama-by-default *before* settings is imported
# anywhere. Settings are cached via lru_cache, so this must run at collection time.
os.environ.setdefault("CHECKPOINT_BACKEND", "memory")
os.environ.setdefault("LLM_BACKEND", "ollama")
os.environ.setdefault("DEFAULT_TENANT_ID", "demo")

import pytest
from resolveai_api.config import get_settings

# Drop the lru_cache so each test session re-reads env above.
get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _reset_stripe_store() -> None:
    """Stripe MCP fake data lives in module-level state; reset between tests."""
    try:
        from mcp_servers.stripe import data as stripe_data

        stripe_data.reset_store()
    except ImportError:
        pass
