"""Layer 2 · Execution side — per-call gVisor sandbox and capability whitelist.

Key trade-off from the design document:
  gVisor does not prevent injection; it limits the blast radius after a
  successful injection. These concerns are often conflated.

M10: `ExecutionSandbox` resolves the effective backend (none, subprocess, or
container) and computes `SandboxPolicy` by capability. For write and
destructive tools, if the selected backend cannot cover an isolation dimension
(subprocess cannot isolate the filesystem or network), it records
`sandbox:degraded:<dims>` in `scope.violations` for auditing and observability.
Behavior remains unchanged when `SANDBOX_MODE=off`, the default.
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
