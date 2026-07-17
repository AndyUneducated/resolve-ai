"""M14 — eval→data flywheel: PII scrub, stratified sampling, clustering, dual gate.

All hermetic + LM-free. The end-to-end trace-sink test uses LLM_BACKEND=fake.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import MemorySaver
from resolveai_api.agents.supervisor import SupervisorGraph
from resolveai_api.config import get_settings
from resolveai_api.eval.flywheel import (
    assert_no_pii,
    cluster_failures,
    dataset_manifest,
    dual_score_gate,
    failure_reason,
    find_pii,
    gate_failed,
    regression_violations,
    render_top_failures_md,
    score_dataset,
    scrub_text,
    stratified_sample,
    to_candidate,
    write_dataset_version,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sample_traces.jsonl"


def _load_fixture() -> list[dict]:
    return [json.loads(line) for line in FIXTURE.read_text().splitlines() if line.strip()]


# ------------------------------ PII scrub ------------------------------


def test_scrub_removes_all_pii_types() -> None:
    raw = "email a@b.com card 4111 1111 1111 1111 ssn 123-45-6789 phone (415) 555-0132 cus_9Z ch_1A"
    scrubbed = scrub_text(raw)
    assert find_pii(scrubbed) == []  # zero residual
    for token in ("[EMAIL]", "[CARD]", "[SSN]", "[PHONE]", "[CUSTOMER_ID]", "[CHARGE_ID]"):
        assert token in scrubbed


def test_assert_no_pii_flags_raw_and_passes_scrubbed() -> None:
    raw = [{"id": "x", "query": "reach me at john@example.com"}]
    assert assert_no_pii(raw) != []
    candidates = [to_candidate(r) for r in raw]
    assert assert_no_pii(candidates) == []


def test_fixture_candidates_have_zero_residual_pii() -> None:
    candidates = [to_candidate(r) for r in _load_fixture()]
    assert assert_no_pii(candidates) == []  # governance hard gate


# ------------------------------ sampling ------------------------------


def test_stratified_sample_is_deterministic_and_capped() -> None:
    records = _load_fixture()
    a = stratified_sample(records, per_stratum=1, seed=42)
    b = stratified_sample(records, per_stratum=1, seed=42)
    assert [r["id"] for r in a] == [r["id"] for r in b]  # reproducible
    # billing|done has 4 records in the fixture → capped at 1 per stratum
    billing_done = [r for r in a if r["intent"] == "billing" and r["outcome"] == "done"]
    assert len(billing_done) == 1


def test_to_candidate_normalizes_and_scrubs() -> None:
    cand = to_candidate(
        {"id": "t", "query": "ch_1A2b for a@b.com", "intent": "billing", "outcome": "done"}
    )
    assert cand["source"] == "prod"
    assert "[CHARGE_ID]" in cand["query"] and "[EMAIL]" in cand["query"]
    assert cand["escalated"] is False and cand["flags"] == []


# ------------------------------ clustering ------------------------------


def test_failure_reason_priority() -> None:
    assert failure_reason({"outcome": "blocked", "blocked_layer": "input"}) == "blocked:input"
    assert failure_reason({"outcome": "done", "escalated": True}) == "escalated"
    assert failure_reason({"outcome": "done", "flags": ["hallucinated:ch_x"]}) == "hallucination"
    assert failure_reason({"outcome": "done", "flags": []}) is None


def test_cluster_failures_counts_and_orders() -> None:
    clusters = cluster_failures(_load_fixture())
    reasons = {(c["intent"], c["reason"]) for c in clusters}
    assert ("other", "blocked:input") in reasons
    assert ("billing", "escalated") in reasons
    # clean auto-resolves are not clusters
    assert all(c["reason"] is not None for c in clusters)
    md = render_top_failures_md(clusters)
    assert "Top failure clusters" in md and "| rank |" in md


# ------------------------------ dataset versioning ------------------------------


def test_write_dataset_version(tmp_path: Path) -> None:
    cases = [to_candidate(r) for r in _load_fixture()]
    paths = write_dataset_version(cases, tmp_path / "v2")
    assert paths["cases"].exists() and paths["manifest"].exists()
    manifest = json.loads(paths["manifest"].read_text())
    assert manifest["count"] == len(cases)
    assert "billing" in manifest["by_intent"]
    written = [json.loads(x) for x in paths["cases"].read_text().splitlines() if x.strip()]
    assert len(written) == len(cases)


def test_dataset_manifest_distribution() -> None:
    manifest = dataset_manifest([to_candidate(r) for r in _load_fixture()])
    assert manifest["count"] == 10
    assert sum(manifest["by_outcome"].values()) == 10


# ------------------------------ dual-scoring gate ------------------------------


def test_score_dataset_metrics() -> None:
    cases = [to_candidate(r) for r in _load_fixture()]
    # expected_block isn't carried by to_candidate; inject for miss-rate coverage
    for c in cases:
        c["expected_block"] = c["outcome"] == "blocked"
    metrics = score_dataset(cases)
    assert 0.0 <= metrics["auto_resolve_rate"] <= 1.0
    assert metrics["guardrail_miss_rate"] == 0.0  # all expected-blocks were blocked
    assert metrics["mean_cost_usd"] > 0.0


def test_regression_violations_catches_each_axis() -> None:
    base = {"auto_resolve_rate": 0.9, "guardrail_miss_rate": 0.0, "mean_cost_usd": 0.010}
    assert regression_violations(dict(base), base) == []  # identical → clean

    dropped = {"auto_resolve_rate": 0.80, "guardrail_miss_rate": 0.0, "mean_cost_usd": 0.010}
    assert any("auto_resolve_rate" in v for v in regression_violations(dropped, base))

    leaky = {"auto_resolve_rate": 0.9, "guardrail_miss_rate": 0.05, "mean_cost_usd": 0.010}
    assert any("guardrail_miss_rate" in v for v in regression_violations(leaky, base))

    pricey = {"auto_resolve_rate": 0.9, "guardrail_miss_rate": 0.0, "mean_cost_usd": 0.020}
    assert any("mean_cost_usd" in v for v in regression_violations(pricey, base))


def test_dual_score_gate_blocks_on_any_dataset() -> None:
    base = {"auto_resolve_rate": 0.9, "guardrail_miss_rate": 0.0, "mean_cost_usd": 0.01}
    baselines = {"legacy": base, "harvested": base}
    # legacy holds, harvested regresses (leaked a should-block) → gate fails
    results = {
        "legacy": dict(base),
        "harvested": {"auto_resolve_rate": 0.9, "guardrail_miss_rate": 0.1, "mean_cost_usd": 0.01},
    }
    gate = dual_score_gate(results=results, baselines=baselines)
    assert gate["legacy"] == []
    assert gate["harvested"] != []
    assert gate_failed(gate) is True


# ------------------------------ trace sink (e2e) ------------------------------


@pytest.fixture()
def _fake_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for layer in ("GUARDRAIL_L1", "GUARDRAIL_L2", "GUARDRAIL_L3", "GUARDRAIL_L4"):
        monkeypatch.setenv(layer, "off")
    monkeypatch.setenv("LLM_BACKEND", "fake")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_trace_sink_appends_scrubbed_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _fake_env: None
) -> None:
    sink = tmp_path / "traces.jsonl"
    monkeypatch.setenv("TRACE_SINK_PATH", str(sink))
    get_settings.cache_clear()

    supervisor = SupervisorGraph(checkpointer=MemorySaver(), mcp_tools=[])
    events = [
        evt
        async for evt in supervisor.stream(
            message="please refund charge ch_1A2b3C, email me at jane@example.com",
            customer_id="c1",
            tenant_id="demo",
            thread_id="t-sink",
        )
    ]
    assert events[-1]["type"] == "done"

    lines = [json.loads(x) for x in sink.read_text().splitlines() if x.strip()]
    assert len(lines) == 1
    record = lines[0]
    assert record["outcome"] == "done"
    # defense-in-depth scrub at write time: no raw PII at rest
    assert "jane@example.com" not in record["query"]
    assert "[EMAIL]" in record["query"] and "[CHARGE_ID]" in record["query"]
    assert find_pii(record["query"]) == []
