"""Async LangGraph checkpointer factory.

行业对齐：用 `AsyncPostgresSaver`（与 FastAPI 异步栈一致）做 dev/prod state
持久化；测试场景下用 `MemorySaver`（LangGraph 官方测试 fixture）。

Checkpoint thread_id 由调用方按 `tenant::customer::thread` 命名（决策 4 · Layer 4）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver

from resolveai_api.config import get_settings
from resolveai_api.guardrails.attribution import flag_enabled
from resolveai_api.guardrails.memory_isolator import MemoryIsolator


class CrossTenantAccessBlocked(PermissionError):
    """Raised when checkpoint namespace does not match request identity."""


class IsolatedCheckpointer(BaseCheckpointSaver):
    """Guard checkpoint access with tenant/customer namespace checks."""

    def __init__(self, inner: BaseCheckpointSaver, *, enabled: bool = True) -> None:
        # Inherit serde from the wrapped saver so downstream LangGraph code that
        # accesses self.serde (writes, versioning) keeps working unchanged.
        super().__init__(serde=getattr(inner, "serde", None))
        self._inner = inner
        self._enabled = enabled

    def _assert_namespace(self, config: dict[str, Any] | None) -> None:
        if not self._enabled:
            return
        if not config:
            return
        configurable = dict(config.get("configurable") or {})
        ns = str(configurable.get("thread_id") or "")
        tenant_id = str(configurable.get("user_tenant_id") or "")
        customer_id = str(configurable.get("user_customer_id") or "")
        if ns and tenant_id and customer_id:
            try:
                MemoryIsolator.assert_match(
                    ns=ns, tenant_id=tenant_id, customer_id=customer_id
                )
            except PermissionError as exc:
                raise CrossTenantAccessBlocked(str(exc)) from exc

    def _assert_tuple_namespace(self, checkpoint_tuple: object, config: dict[str, Any]) -> None:
        if not self._enabled:
            return
        if checkpoint_tuple is None:
            return
        tuple_config = getattr(checkpoint_tuple, "config", None)
        if not isinstance(tuple_config, dict):
            return
        configurable = dict(tuple_config.get("configurable") or {})
        tuple_ns = str(configurable.get("thread_id") or "")
        if not tuple_ns:
            return
        request_conf = dict(config.get("configurable") or {})
        tenant_id = str(request_conf.get("user_tenant_id") or "")
        customer_id = str(request_conf.get("user_customer_id") or "")
        if tenant_id and customer_id:
            try:
                MemoryIsolator.assert_match(
                    ns=tuple_ns, tenant_id=tenant_id, customer_id=customer_id
                )
            except PermissionError as exc:
                raise CrossTenantAccessBlocked(str(exc)) from exc

    def get_tuple(self, config: dict[str, Any]) -> Any:
        self._assert_namespace(config)
        checkpoint_tuple = self._inner.get_tuple(config)
        self._assert_tuple_namespace(checkpoint_tuple, config)
        return checkpoint_tuple

    async def aget_tuple(self, config: dict[str, Any]) -> Any:
        self._assert_namespace(config)
        checkpoint_tuple = await self._inner.aget_tuple(config)
        self._assert_tuple_namespace(checkpoint_tuple, config)
        return checkpoint_tuple

    def put(
        self,
        config: dict[str, Any],
        checkpoint: Any,
        metadata: Any,
        new_versions: Any,
    ) -> dict[str, Any]:
        self._assert_namespace(config)
        return self._inner.put(config, checkpoint, metadata, new_versions)

    async def aput(
        self,
        config: dict[str, Any],
        checkpoint: Any,
        metadata: Any,
        new_versions: Any,
    ) -> dict[str, Any]:
        self._assert_namespace(config)
        return await self._inner.aput(config, checkpoint, metadata, new_versions)

    def list(
        self,
        config: dict[str, Any] | None,
        *,
        filter: dict[str, Any] | None = None,
        before: dict[str, Any] | None = None,
        limit: int | None = None,
    ) -> Iterator[Any]:
        self._assert_namespace(config)
        request_conf = dict((config or {}).get("configurable") or {})
        for item in self._inner.list(config, filter=filter, before=before, limit=limit):
            self._assert_tuple_namespace(item, {"configurable": request_conf})
            yield item

    def put_writes(
        self,
        config: dict[str, Any],
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        self._assert_namespace(config)
        self._inner.put_writes(config, writes, task_id, task_path)

    async def alist(
        self,
        config: dict[str, Any] | None,
        *,
        filter: dict[str, Any] | None = None,
        before: dict[str, Any] | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[Any]:
        self._assert_namespace(config)
        request_conf = dict((config or {}).get("configurable") or {})
        async for item in self._inner.alist(
            config, filter=filter, before=before, limit=limit
        ):
            self._assert_tuple_namespace(item, {"configurable": request_conf})
            yield item

    async def aput_writes(
        self,
        config: dict[str, Any],
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        self._assert_namespace(config)
        await self._inner.aput_writes(config, writes, task_id, task_path)

    def get_next_version(self, current: Any, channel: None) -> Any:
        return self._inner.get_next_version(current, channel)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


@asynccontextmanager
async def lifespan_checkpointer() -> AsyncIterator[BaseCheckpointSaver]:
    """Yield a checkpointer for the FastAPI lifespan; closes the pg conn on shutdown."""
    settings = get_settings()
    l4_enabled = flag_enabled(getattr(settings, "guardrail_l4", "on"))

    if settings.checkpoint_backend == "memory":
        yield IsolatedCheckpointer(MemorySaver(), enabled=l4_enabled)
        return

    if settings.checkpoint_backend != "postgres":
        raise ValueError(
            f"Unsupported CHECKPOINT_BACKEND={settings.checkpoint_backend!r}; "
            "expected 'postgres' or 'memory'."
        )

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    async with AsyncPostgresSaver.from_conn_string(settings.psycopg_dsn) as saver:
        await saver.setup()  # idempotent — creates checkpoint tables on first run
        yield IsolatedCheckpointer(saver, enabled=l4_enabled)
