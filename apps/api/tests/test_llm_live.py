"""Live Ollama integration smoke tests (no LLM mocks).

Run explicitly — not part of default CI:
    uv run python -m pytest apps/api/tests/test_llm_live.py -v

Requires: `ollama serve` and models from .env (default qwen2.5:7b).
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from langchain_core.messages import HumanMessage, SystemMessage
from resolveai_api.agents.billing_graph import Plan
from resolveai_api.agents.triage import SYSTEM_PROMPT, TriageAgent, TriageOutput
from resolveai_api.config import get_settings
from resolveai_api.core.llm import make_llm, make_structured_llm

pytestmark = pytest.mark.integration

OLLAMA_PROBE_TIMEOUT = 5.0
LLM_CALL_TIMEOUT = 180.0


def _ollama_base_url() -> str:
    get_settings.cache_clear()
    return get_settings().ollama_base_url.rstrip("/")


@pytest.fixture(scope="module")
def ollama_available() -> str:
    """Skip module if Ollama is down or configured model is missing."""
    base = _ollama_base_url()
    try:
        r = httpx.get(f"{base}/api/tags", timeout=OLLAMA_PROBE_TIMEOUT)
        r.raise_for_status()
    except Exception as exc:
        pytest.skip(f"Ollama not reachable at {base}: {exc}")

    names = {m["name"] for m in r.json().get("models", [])}
    # Tags may be "qwen2.5:7b" or include digest suffixes on some installs.
    settings = get_settings()
    for required in (settings.triage_model, settings.vertical_model):
        if not any(n == required or n.startswith(f"{required}:") for n in names):
            pytest.skip(f"Model {required!r} not in ollama list: {sorted(names)[:8]}...")
    return base


async def _with_timeout(coro, *, seconds: float = LLM_CALL_TIMEOUT):
    return await asyncio.wait_for(coro, timeout=seconds)


@pytest.mark.asyncio
async def test_ollama_chat_roundtrip(ollama_available: str) -> None:
    """Basic ChatOllama connectivity for triage tier."""
    llm = make_llm("triage", temperature=0.0)
    msg = await _with_timeout(
        llm.ainvoke([HumanMessage(content="Reply with exactly: pong")])
    )
    text = str(getattr(msg, "content", msg)).lower()
    assert text, "empty model response"
    assert "pong" in text or len(text) > 0


@pytest.mark.asyncio
async def test_live_triage_structured_billing(ollama_available: str) -> None:
    """Structured TriageOutput via with_structured_output (real 7B)."""
    agent = TriageAgent.default()
    state = {
        "messages": [
            HumanMessage(
                content="I was double-charged $99 on my account last month. Please refund."
            )
        ],
        "customer_id": "cus_demo_001",
        "tenant_id": "demo",
        "thread_id": "live-triage",
        "tool_calls": [],
        "guardrail_flags": [],
    }
    out = await _with_timeout(agent.run(state))
    summary = out.get("ticket_summary") or {}
    intent = summary.get("intent")
    assert intent in ("billing", "other", "technical"), f"unexpected intent: {intent!r}"
    # Strong signal for engineering smoke — billing phrasing should usually route billing.
    if intent != "billing":
        pytest.fail(
            f"expected billing intent for refund message, got {intent!r}; "
            f"summary={summary!r}"
        )
    assert summary.get("confidence", 0) >= 0


@pytest.mark.asyncio
async def test_live_vertical_structured_plan(ollama_available: str) -> None:
    """One vertical-tier structured Plan call (billing planner path)."""
    llm = make_structured_llm("vertical", Plan, temperature=0.0)
    prompt = (
        "Customer was double-charged $99. Plan steps to list charges via Stripe "
        "and issue a refund if duplicate."
    )
    result = await _with_timeout(
        llm.ainvoke(
            [
                SystemMessage(content="You are a billing support planner. Output a short plan."),
                HumanMessage(content=prompt),
            ]
        )
    )
    plan = result if isinstance(result, Plan) else Plan.model_validate(result)
    assert isinstance(plan.steps, list)
    assert len(plan.steps) >= 1, f"empty plan steps: {plan!r}"
    joined = " ".join(plan.steps).lower()
    assert any(
        kw in joined for kw in ("charge", "refund", "stripe", "payment", "billing")
    ), f"plan does not mention billing actions: {plan.steps!r}"


@pytest.mark.asyncio
async def test_live_triage_direct_structured(ollama_available: str) -> None:
    """Direct make_structured_llm path (bypasses agent) — catches schema/JSON issues."""
    llm = make_structured_llm("triage", TriageOutput, temperature=0.0)
    result = await _with_timeout(
        llm.ainvoke(
            [
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content="My API returns 502 errors intermittently."),
            ]
        )
    )
    out = result if isinstance(result, TriageOutput) else TriageOutput.model_validate(result)
    assert out.intent in ("technical", "other", "billing", "escalation")
