"""Layer 2 · 执行侧 — gVisor per-call 沙箱 + capability 白名单。

关键 trade-off（设计文档原文）：
  gVisor 本身**不防 injection**，它防的是 **injection 成功之后的 blast radius**。
  这两件事经常被混。

M10：`ExecutionSandbox` 现在解析**有效后端**（none / subprocess / container）并按
capability 计算 `SandboxPolicy`；对 write / destructive 工具，若所选后端无法覆盖
某些隔离维度（subprocess 无法隔离 fs / network），记 `sandbox:degraded:<dims>` 到
`scope.violations`（供审计 / 观测）。`SANDBOX_MODE=off`（默认）时行为与之前一致。
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from resolveai_api.config import get_settings
from resolveai_api.guardrails.sandbox import (
    SandboxPolicy,
    degraded_dimensions,
    select_backend,
)

_GATED_CAPABILITIES = ("write", "destructive")


@dataclass
class SandboxScope:
    tool: str
    mode: str
    backend: str = "none"
    capability: str = "read"
    policy: SandboxPolicy | None = None
    started_at: float = field(default_factory=time.perf_counter)
    duration_ms: float = 0.0
    violations: list[str] = field(default_factory=list)


class ExecutionSandbox:
    """Execution scope + policy/backend selection for sandboxed MCP tool calls."""

    def __init__(self) -> None:
        settings = get_settings()
        self.runtime = settings.gvisor_runtime
        self.mode = str(getattr(settings, "sandbox_mode", "off")).strip().lower()
        self._backend = (
            select_backend(mode=self.mode, runtime=self.runtime)
            if self.mode not in ("off", "")
            else "none"
        )

    @asynccontextmanager
    async def scope(self, *, tool: str, capability: str = "read"):
        scope = SandboxScope(
            tool=tool, mode=self.mode, backend=self._backend, capability=capability
        )
        if self._backend != "none":
            scope.policy = SandboxPolicy.for_capability(capability)
            # Gated tools demand full containment; flag what this backend can't do.
            if capability in _GATED_CAPABILITIES:
                missing = degraded_dimensions(self._backend)
                if missing:
                    scope.violations.append(
                        "sandbox:degraded:" + ",".join(sorted(missing))
                    )
        try:
            yield scope
        finally:
            scope.duration_ms = (time.perf_counter() - scope.started_at) * 1000.0
