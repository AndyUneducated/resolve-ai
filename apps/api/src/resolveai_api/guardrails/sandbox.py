"""Layer 2 · Real blast-radius sandboxing (M10).

Design:
- **Container tier (gVisor `runsc` / `runc`)**: the only backend that isolates
  the filesystem, network, and resources together. Each tool runs in a
  disposable container with `--network`, `--read-only`, `--memory`,
  `--pids-limit`, `--ulimit cpu/fsize`, and `--cap-drop=ALL`. This tier is
  available only when Docker and the corresponding runtime are installed.
- **Subprocess tier (POSIX rlimit + wall timeout)**: fallback when Docker is
  unavailable. It enforces CPU time, memory, process count, file size, and
  wall-clock timeout, but cannot isolate the filesystem or network. Those two
  dimensions are marked `degraded`, quantifying why true isolation needs gVisor.

Key trade-off: sandboxing does not prevent injection; it limits the blast
radius after a successful injection.

`run_sandboxed()` runs real probes for the escape-test harness
(`scripts/eval_sandbox.py`). Production MCP tools are in-process async calls;
`ExecutionSandbox` therefore selects a backend and marks degraded dimensions.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from enum import StrEnum

# All isolation dimensions. Containers cover all of them; subprocesses cover
# everything except network and filesystem isolation.
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
        """Stricter budget for gated (write/destructive) tools than for reads.

        The base budget is sourced from `Settings` (the `SANDBOX_*` env knobs) so the
        documented config is actually authoritative instead of dead. Env-unset values
        equal the dataclass defaults, so this is behaviour-preserving out of the box.
        """
        from resolveai_api.config import get_settings

        s = get_settings()
        base = cls(
            cpu_seconds=s.sandbox_cpu_seconds,
            memory_mb=s.sandbox_memory_mb,
            wall_timeout_s=s.sandbox_wall_timeout_s,
            max_processes=s.sandbox_max_processes,
            max_file_bytes=s.sandbox_max_file_mb * 1024 * 1024,
            network=s.sandbox_network,
        )
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
