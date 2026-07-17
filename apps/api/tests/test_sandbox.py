"""M10 sandbox — real OS-level containment + isolation contract (no LM, no docker).

The subprocess-tier tests spawn short, strictly-bounded child processes to prove
the rlimit / wall-timeout enforcement is real; the container-tier tests are pure
(command construction + runtime detection), so they need no docker.
"""

from __future__ import annotations

import os
import sys
import tempfile

import pytest
from resolveai_api.config import get_settings
from resolveai_api.core.executor import Executor
from resolveai_api.guardrails.sandbox import (
    ALL_DIMENSIONS,
    SandboxOutcome,
    SandboxPolicy,
    build_container_argv,
    container_runtime_available,
    degraded_dimensions,
    enforced_dimensions,
    run_sandboxed,
    select_backend,
)

posix_only = pytest.mark.skipif(os.name != "posix", reason="rlimits are POSIX-only")


def test_policy_for_capability_tightens_gated_tools() -> None:
    read = SandboxPolicy.for_capability("read")
    destructive = SandboxPolicy.for_capability("destructive")
    assert destructive.network == "none"
    assert destructive.read_only_fs is True
    assert read.read_only_fs is True  # default is safe


def test_dimensions_subprocess_cannot_isolate_fs_or_network() -> None:
    sub = enforced_dimensions("subprocess")
    assert "cpu" in sub and "file_size" in sub and "wall_time" in sub
    assert "network" not in sub and "filesystem" not in sub
    degraded = degraded_dimensions("subprocess")
    assert degraded == {"network", "filesystem"}
    # Container isolates everything.
    assert enforced_dimensions("container") == set(ALL_DIMENSIONS)
    assert degraded_dimensions("container") == set()


@posix_only
def test_run_sandboxed_allows_benign_command() -> None:
    result = run_sandboxed([sys.executable, "-c", "print('hi')"])
    assert result.outcome == SandboxOutcome.OK
    assert "hi" in result.stdout
    assert result.contained is False


@posix_only
def test_run_sandboxed_wall_timeout_contains_cpu_spin() -> None:
    policy = SandboxPolicy(cpu_seconds=30, wall_timeout_s=1.0)
    result = run_sandboxed(
        [sys.executable, "-c", "\nwhile True:\n    pass\n"], policy=policy
    )
    assert result.outcome == SandboxOutcome.TIMEOUT
    assert result.contained is True


@posix_only
def test_run_sandboxed_file_size_limit_contains_disk_bomb() -> None:
    fd, path = tempfile.mkstemp(prefix="resolveai_sbx_test_")
    os.close(fd)
    code = (
        f"f=open({path!r},'wb')\n"
        "f.write(b'x'*(64*1024*1024))\n"
        "f.flush()\n"
        "print('ESC:wrote')"
    )
    policy = SandboxPolicy(
        max_file_bytes=1 * 1024 * 1024, wall_timeout_s=5.0, memory_mb=512
    )
    try:
        result = run_sandboxed([sys.executable, "-c", code], policy=policy)
        assert result.contained is True, result
        assert "ESC:wrote" not in result.stdout
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_build_container_argv_has_isolation_flags() -> None:
    policy = SandboxPolicy(memory_mb=256, max_processes=64, cpu_seconds=5)
    argv = build_container_argv(
        ["python", "-c", "print(1)"], policy=policy, runtime="runsc"
    )
    joined = " ".join(argv)
    assert "--runtime=runsc" in argv
    assert "--network=none" in argv
    assert "--read-only" in argv
    assert "--memory=256m" in argv
    assert "--pids-limit=64" in argv
    assert "--cap-drop=ALL" in argv
    assert "cpu=5" in argv and "fsize=" in joined


def test_container_runtime_available_returns_bool() -> None:
    assert isinstance(container_runtime_available("runsc"), bool)


def test_select_backend_resolves_modes() -> None:
    assert select_backend(mode="off") == "none"
    assert select_backend(mode="") == "none"
    assert select_backend(mode="subprocess") == "subprocess"
    assert select_backend(mode="container") == "container"
    assert select_backend(mode="auto") in ("subprocess", "container")


@pytest.mark.asyncio
async def test_execution_sandbox_off_records_no_violations() -> None:
    """Default (SANDBOX_MODE=off) → metadata only, byte-identical to before."""
    get_settings.cache_clear()
    executor = Executor()
    async with executor.sandbox.scope(tool="stripe.refund", capability="destructive") as scope:
        pass
    assert scope.violations == []
    assert scope.backend == "none"


@pytest.mark.asyncio
async def test_execution_sandbox_flags_degraded_for_destructive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SANDBOX_MODE", "subprocess")
    get_settings.cache_clear()
    try:
        executor = Executor()
        async with executor.sandbox.scope(
            tool="stripe.refund", capability="destructive"
        ) as scope:
            pass
        assert scope.backend == "subprocess"
        assert any(v.startswith("sandbox:degraded:") for v in scope.violations)
        degraded_flag = next(v for v in scope.violations if v.startswith("sandbox:degraded:"))
        assert "network" in degraded_flag and "filesystem" in degraded_flag
        # Read-capability tools are not gated → no degraded flag.
        async with executor.sandbox.scope(tool="stripe.get_charge", capability="read") as read_scope:
            pass
        assert read_scope.violations == []
    finally:
        get_settings.cache_clear()
