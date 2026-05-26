"""Layer 2 · 执行侧 — gVisor per-call 沙箱 + capability 白名单。

关键 trade-off（设计文档原文）：
  gVisor 本身**不防 injection**，它防的是 **injection 成功之后的 blast radius**。
  这两件事经常被混。
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from resolveai_api.config import get_settings


@dataclass
class SandboxScope:
    tool: str
    mode: str
    started_at: float = field(default_factory=time.perf_counter)
    duration_ms: float = 0.0
    violations: list[str] = field(default_factory=list)


class ExecutionSandbox:
    """Execution scope metadata for sandboxed MCP tool calls."""

    def __init__(self) -> None:
        settings = get_settings()
        self.runtime = settings.gvisor_runtime
        self.mode = str(getattr(settings, "sandbox_mode", "off")).strip().lower()

    @asynccontextmanager
    async def scope(self, *, tool: str):
        scope = SandboxScope(tool=tool, mode=self.mode)
        try:
            yield scope
        finally:
            scope.duration_ms = (time.perf_counter() - scope.started_at) * 1000.0
