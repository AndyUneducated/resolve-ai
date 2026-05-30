from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from resolveai_api.core.checkpointer import CrossTenantAccessBlocked, IsolatedCheckpointer
from resolveai_api.guardrails.eval_scoring import build_summary


@dataclass
class _Tuple:
    config: dict[str, Any]


class _FakeSaver:
    def __init__(self, stored_ns: str) -> None:
        self.stored_ns = stored_ns

    async def aget_tuple(self, config: dict[str, Any]) -> _Tuple:
        return _Tuple(config={"configurable": {"thread_id": self.stored_ns}})


def _mk_row(
    *,
    row_id: str,
    category: str,
    profile: str,
    blocked: bool,
    expected_layer: str = "input",
    flags: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": row_id,
        "category": category,
        "profile": profile,
        "expected_block_layer": expected_layer,
        "blocked": blocked,
        "flags": flags or [],
    }


def test_eval_scoring_builds_expected_tables() -> None:
    rows = [
        _mk_row(row_id="jb-1", category="jailbreak", profile="baseline", blocked=True),
        _mk_row(row_id="jb-2", category="jailbreak", profile="baseline", blocked=False),
        _mk_row(row_id="jb-1", category="jailbreak", profile="l1_only", blocked=True),
        _mk_row(row_id="jb-2", category="jailbreak", profile="l1_only", blocked=False),
        _mk_row(row_id="jb-1", category="jailbreak", profile="l3_only", blocked=False),
        _mk_row(row_id="jb-2", category="jailbreak", profile="l3_only", blocked=False),
        _mk_row(row_id="jb-1", category="jailbreak", profile="l4_only", blocked=False),
        _mk_row(row_id="jb-2", category="jailbreak", profile="l4_only", blocked=False),
        _mk_row(row_id="jb-1", category="jailbreak", profile="ablate_l1", blocked=False),
        _mk_row(row_id="jb-2", category="jailbreak", profile="ablate_l1", blocked=False),
        _mk_row(row_id="jb-1", category="jailbreak", profile="ablate_l3", blocked=True),
        _mk_row(row_id="jb-2", category="jailbreak", profile="ablate_l3", blocked=False),
        _mk_row(row_id="jb-1", category="jailbreak", profile="ablate_l4", blocked=True),
        _mk_row(row_id="jb-2", category="jailbreak", profile="ablate_l4", blocked=False),
        _mk_row(
            row_id="benign-1",
            category="benign",
            profile="baseline",
            blocked=True,
            expected_layer="none",
            flags=["policy:unauthorized_concession"],
        ),
        _mk_row(
            row_id="benign-2",
            category="benign",
            profile="baseline",
            blocked=False,
            expected_layer="none",
        ),
    ]
    summary = build_summary(rows)
    attribution_row = next(
        row for row in summary["layer_attribution"] if row["category"] == "jailbreak"
    )
    assert attribution_row["Layer 1"] == "50.0%"
    assert attribution_row["Layer 3"] == "0.0%"
    assert attribution_row["Layer 4"] == "0.0%"
    assert attribution_row["Miss"] == "50.0%"

    ablation = {row["profile"]: row for row in summary["ablation"]}
    assert ablation["baseline"]["block_rate"] == "50.0%"
    assert ablation["baseline"]["false_positive"] == "50.0%"
    assert ablation["ablate_l1"]["block_rate"] == "0.0%"

    fp = summary["false_positive"]
    assert fp["blocked_benign"] == 1
    assert fp["reasons"]["policy:unauthorized_concession"] == 1


@pytest.mark.asyncio
async def test_l4_blocks_cross_tenant_when_enabled() -> None:
    saver = IsolatedCheckpointer(_FakeSaver(stored_ns="tenant-a::cust-1::thread-1"), enabled=True)
    config = {
        "configurable": {
            "thread_id": "tenant-b::cust-1::thread-1",
            "user_tenant_id": "tenant-b",
            "user_customer_id": "cust-1",
        }
    }
    with pytest.raises(CrossTenantAccessBlocked):
        await saver.aget_tuple(config)


@pytest.mark.asyncio
async def test_l4_ablation_allows_cross_tenant_when_disabled() -> None:
    saver = IsolatedCheckpointer(_FakeSaver(stored_ns="tenant-a::cust-1::thread-1"), enabled=False)
    config = {
        "configurable": {
            "thread_id": "tenant-b::cust-1::thread-1",
            "user_tenant_id": "tenant-b",
            "user_customer_id": "cust-1",
        }
    }
    result = await saver.aget_tuple(config)
    assert result.config["configurable"]["thread_id"] == "tenant-a::cust-1::thread-1"
