"""Pytest fixtures — keep tests hermetic (no Postgres, no real LLM)."""

from __future__ import annotations

import os

# Force in-memory checkpointer & ollama-by-default *before* settings is imported
# anywhere. Settings are cached via lru_cache, so this must run at collection time.
os.environ.setdefault("CHECKPOINT_BACKEND", "memory")
os.environ.setdefault("LLM_BACKEND", "ollama")
os.environ.setdefault("DEFAULT_TENANT_ID", "demo")
os.environ.setdefault("SANDBOX_MODE", "off")

# Guardrail toggles are AUTHORITATIVE for the hermetic suite: assign directly
# (not setdefault) so an ambient `export GUARDRAIL_L3=on` from an outer shell or
# live-eval script cannot leak in and make mock-LLM tests hit the real output
# guardrail (which previously turned `done` into `blocked`). Tests that need a
# specific layer toggled use monkeypatch.setenv + get_settings.cache_clear(),
# which overrides these at run time and restores afterwards.
os.environ["GUARDRAIL_L1"] = "off"
os.environ["GUARDRAIL_L2"] = "off"
os.environ["GUARDRAIL_L3"] = "off"
os.environ["GUARDRAIL_L4"] = "on"

import pytest
from resolveai_api.config import get_settings

# Drop the lru_cache so each test session re-reads env above.
get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _reset_mcp_stores() -> None:
    """All mock MCP servers keep module-level state; reset between tests."""
    for module_name in (
        "mcp_servers.stripe.data",
        "mcp_servers.zendesk.data",
        "mcp_servers.slack.data",
        "mcp_servers.salesforce.data",
        "mcp_servers.intercom.data",
    ):
        try:
            module = __import__(module_name, fromlist=["reset_store"])
        except ImportError:
            continue
        reset = getattr(module, "reset_store", None)
        if callable(reset):
            reset()
