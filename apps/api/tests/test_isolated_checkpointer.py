from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from resolveai_api.core.checkpointer import IsolatedCheckpointer


@dataclass
class _Tuple:
    config: dict[str, Any]


class _FakeSaver:
    def __init__(self, stored_ns: str) -> None:
        self.stored_ns = stored_ns

    async def aget_tuple(self, config: dict[str, Any]) -> _Tuple:
        return _Tuple(config={"configurable": {"thread_id": self.stored_ns}})

    async def aput(
        self,
        config: dict[str, Any],
        checkpoint: Any,
        metadata: Any,
        new_versions: Any,
    ) -> dict[str, Any]:
        return config


@pytest.mark.asyncio
async def test_isolated_checkpointer_blocks_cross_tenant_read() -> None:
    saver = IsolatedCheckpointer(_FakeSaver(stored_ns="tenant-a::cust-1::thread-1"))
    config = {
        "configurable": {
            "thread_id": "tenant-b::cust-1::thread-1",
            "user_tenant_id": "tenant-b",
            "user_customer_id": "cust-1",
        }
    }
    with pytest.raises(PermissionError):
        await saver.aget_tuple(config)


@pytest.mark.asyncio
async def test_isolated_checkpointer_allows_same_tenant_customer() -> None:
    saver = IsolatedCheckpointer(_FakeSaver(stored_ns="tenant-a::cust-1::thread-1"))
    config = {
        "configurable": {
            "thread_id": "tenant-a::cust-1::thread-1",
            "user_tenant_id": "tenant-a",
            "user_customer_id": "cust-1",
        }
    }
    result = await saver.aget_tuple(config)
    assert result.config["configurable"]["thread_id"] == "tenant-a::cust-1::thread-1"


@pytest.mark.asyncio
async def test_isolated_checkpointer_blocks_cross_tenant_write() -> None:
    saver = IsolatedCheckpointer(_FakeSaver(stored_ns="tenant-a::cust-1::thread-1"))
    config = {
        "configurable": {
            "thread_id": "tenant-x::cust-1::thread-1",
            "user_tenant_id": "tenant-a",
            "user_customer_id": "cust-1",
        }
    }
    with pytest.raises(PermissionError):
        await saver.aput(config, checkpoint={}, metadata={}, new_versions={})
