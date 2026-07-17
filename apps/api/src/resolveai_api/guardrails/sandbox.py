"""Layer 2 · 真实 blast-radius 沙箱（M10）。

设计（诚实版）：
- **container 层（gVisor `runsc` / `runc`）**：唯一能同时隔离**文件系统 + 网络 +
  资源**的后端。工具打包为一次性容器：`--network`、`--read-only`、`--memory`、
  `--pids-limit`、`--ulimit cpu/fsize`、`--cap-drop=ALL`。仅在宿主装了 docker +
  对应 runtime 时可用。
- **subprocess 层（POSIX rlimit + wall timeout）**：无 docker 时的兜底。能强制
  **CPU 时间 / 内存 / 进程数 / 文件大小 / 墙钟超时**，但**无法**隔离文件系统与
  网络 —— 这两维标记为 `degraded`，正是"要真隔离必须上 gVisor"的量化依据。

关键 trade-off：沙箱**不防 injection**，防的是 **injection 得手后的爆炸半径**。

`run_sandboxed()` 是给逃逸测试 harness（`scripts/eval_sandbox.py`）跑真实探针用的；
线上 MCP 工具是进程内 async 调用，`ExecutionSandbox` 据此选后端 + 标记降级维度。
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from enum import StrEnum

# 所有隔离维度。container 全覆盖；subprocess 覆盖除 network / filesystem 外的部分。
ALL_DIMENSIONS: tuple[str, ...] = (
    "cpu",
    "memory",
    "processes",
    "file_size",
    "wall_time",
    "network",
    "filesystem",
)
_SUBPROCESS_DIMENSIONS: frozenset[str] = frozenset(
    {"cpu", "memory", "processes", "file_size", "wall_time"}
)
_CONTAINER_DIMENSIONS: frozenset[str] = frozenset(ALL_DIMENSIONS)


class SandboxOutcome(StrEnum):
    OK = "ok"  # command completed with exit 0 (an *escape* if the probe expected containment)
    TIMEOUT = "timeout"  # killed by wall-clock timeout
    RESOURCE_LIMIT = "resource_limit"  # killed by a signal (SIGXCPU/SIGXFSZ/SIGKILL...)
    TOOL_ERROR = "tool_error"  # non-zero exit from the command itself
    UNAVAILABLE = "unavailable"  # backend could not run at all


@dataclass(slots=True)
class SandboxPolicy:
    """Per tool-call resource + isolation contract."""

    cpu_seconds: int = 5
    memory_mb: int = 256
    wall_timeout_s: float = 10.0
    max_processes: int = 64
    max_file_bytes: int = 16 * 1024 * 1024
    network: str = "none"  # "none" | "allowlist"
    read_only_fs: bool = True

    @classmethod
    def for_capability(cls, capability: str, **overrides: object) -> SandboxPolicy:
        """Stricter budget for gated (write/destructive) tools than for reads."""
        base = cls()
        if capability in ("write", "destructive"):
            base.cpu_seconds = min(base.cpu_seconds, 5)
            base.wall_timeout_s = min(base.wall_timeout_s, 10.0)
            base.network = "none"
            base.read_only_fs = True
        for key, value in overrides.items():
            setattr(base, key, value)
        return base


@dataclass(slots=True)
class SandboxResult:
    outcome: SandboxOutcome
    returncode: int | None
    stdout: str
    stderr: str
    duration_ms: float
    backend: str
    enforced: tuple[str, ...]
    degraded: tuple[str, ...]

    @property
    def contained(self) -> bool:
        """A probe was contained if it did NOT complete cleanly."""
        return self.outcome in (
            SandboxOutcome.TIMEOUT,
            SandboxOutcome.RESOURCE_LIMIT,
            SandboxOutcome.TOOL_ERROR,
        )


def enforced_dimensions(backend: str) -> frozenset[str]:
    if backend == "container":
        return _CONTAINER_DIMENSIONS
    if backend == "subprocess":
        return _SUBPROCESS_DIMENSIONS
    return frozenset()


def degraded_dimensions(backend: str) -> frozenset[str]:
    """Dimensions the required policy wants but this backend can't enforce."""
    return frozenset(ALL_DIMENSIONS) - enforced_dimensions(backend)


def _apply_rlimits(policy: SandboxPolicy) -> None:  # pragma: no cover - runs in child
    """Best-effort POSIX rlimits, applied post-fork/pre-exec in the child.

    Each limit is guarded: macOS ignores/forbids some (RLIMIT_AS, RLIMIT_NPROC),
    so we never let a failed setrlimit abort the child.
    """
    import resource

    def _set(res_name: str, soft: int, hard: int | None = None) -> None:
        res = getattr(resource, res_name, None)
        if res is None:
            return
        with contextlib.suppress(ValueError, OSError):
            resource.setrlimit(res, (soft, hard if hard is not None else soft))

    if policy.cpu_seconds:
        _set("RLIMIT_CPU", policy.cpu_seconds, policy.cpu_seconds + 1)
    if policy.max_file_bytes:
        _set("RLIMIT_FSIZE", policy.max_file_bytes)
    if policy.memory_mb:
        _set("RLIMIT_AS", policy.memory_mb * 1024 * 1024)
    if policy.max_processes:
        _set("RLIMIT_NPROC", policy.max_processes)


def run_sandboxed(
    argv: list[str],
    *,
    policy: SandboxPolicy | None = None,
    stdin: str | None = None,
) -> SandboxResult:
    """Run `argv` under the subprocess backend (POSIX rlimits + wall timeout).

    Deterministic and dependency-free; used by the escape-test harness to
    *quantify* what this tier contains vs. what needs gVisor.
    """
    policy = policy or SandboxPolicy()
    backend = "subprocess"
    posix = os.name == "posix"
    enforced = tuple(sorted(enforced_dimensions(backend))) if posix else ()
    degraded = (
        tuple(sorted(degraded_dimensions(backend)))
        if posix
        else tuple(sorted(ALL_DIMENSIONS))
    )
    preexec = (lambda: _apply_rlimits(policy)) if posix else None

    # Minimal env: drop inherited secrets so an escaped probe can't read them.
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/tmp"}

    start = time.perf_counter()
    try:
        proc = subprocess.run(
            argv,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=policy.wall_timeout_s,
            preexec_fn=preexec,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        duration_ms = (time.perf_counter() - start) * 1000.0
        return SandboxResult(
            outcome=SandboxOutcome.TIMEOUT,
            returncode=None,
            stdout=(exc.stdout or b"").decode(errors="replace")
            if isinstance(exc.stdout, bytes)
            else (exc.stdout or ""),
            stderr="wall_timeout",
            duration_ms=duration_ms,
            backend=backend,
            enforced=enforced,
            degraded=degraded,
        )
    except (OSError, ValueError) as exc:
        duration_ms = (time.perf_counter() - start) * 1000.0
        return SandboxResult(
            outcome=SandboxOutcome.UNAVAILABLE,
            returncode=None,
            stdout="",
            stderr=f"{type(exc).__name__}: {exc}",
            duration_ms=duration_ms,
            backend=backend,
            enforced=enforced,
            degraded=degraded,
        )

    duration_ms = (time.perf_counter() - start) * 1000.0
    rc = proc.returncode
    if rc == 0:
        outcome = SandboxOutcome.OK
    elif rc is not None and rc < 0:  # killed by signal N → returncode == -N
        outcome = SandboxOutcome.RESOURCE_LIMIT
    else:
        outcome = SandboxOutcome.TOOL_ERROR
    return SandboxResult(
        outcome=outcome,
        returncode=rc,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
        duration_ms=duration_ms,
        backend=backend,
        enforced=enforced,
        degraded=degraded,
    )


def build_container_argv(
    argv: list[str],
    *,
    policy: SandboxPolicy,
    runtime: str = "runsc",
    image: str = "resolveai/mcp-servers:dev",
) -> list[str]:
    """Build a `docker run` command that fully isolates fs + network + resources.

    Pure function (no docker needed) so the isolation contract is unit-testable.
    """
    cmd = [
        "docker",
        "run",
        "--rm",
        f"--runtime={runtime}",
        f"--network={'none' if policy.network == 'none' else 'bridge'}",
        f"--memory={policy.memory_mb}m",
        "--cpus=1.0",
        f"--pids-limit={policy.max_processes}",
        "--ulimit",
        f"cpu={policy.cpu_seconds}",
        "--ulimit",
        f"fsize={policy.max_file_bytes}",
        "--cap-drop=ALL",
        "--security-opt",
        "no-new-privileges",
    ]
    if policy.read_only_fs:
        cmd.append("--read-only")
    cmd.append(image)
    cmd.extend(argv)
    return cmd


def container_runtime_available(runtime: str = "runsc") -> bool:
    """True iff docker is on PATH and reports `runtime`. Safe (no network)."""
    docker = shutil.which("docker")
    if not docker:
        return False
    try:
        out = subprocess.run(
            [docker, "info", "--format", "{{json .Runtimes}}"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return runtime in (out.stdout or "")


def select_backend(*, mode: str, runtime: str = "runsc") -> str:
    """Resolve the effective backend: `none` | `subprocess` | `container`.

    `off`/empty → none (metadata only); `subprocess`/`container` force a tier;
    `on`/`auto` → container when the runtime is available, else subprocess.
    """
    normalized = str(mode).strip().lower()
    if normalized in ("off", ""):
        return "none"
    if normalized in ("subprocess", "container"):
        return normalized
    return "container" if container_runtime_available(runtime) else "subprocess"
