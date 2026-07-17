"""Guardrail attribution and blocking semantics.

This module is the single source of truth for:
- which flag belongs to which guardrail layer
- which flags are hard-blocking
- named guardrail profiles used by ablation/eval harnesses
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum


class Layer(StrEnum):
    INPUT = "input"
    EXEC = "exec"
    OUTPUT = "output"
    MEMORY = "memory"


class GuardrailOutcome(StrEnum):
    BLOCKED = "blocked"
    MITIGATED = "mitigated"
    LEAKED = "leaked"
    CLEAN = "clean"


class BlockKind(StrEnum):
    """Why a request was blocked — for SLO reporting (M10).

    Distinguishes a *real* catch from an *availability* failure so dashboards can
    separate "we stopped something harmful" from "a guard was down and we chose
    safety over availability".
    """

    TRUE_POSITIVE = "true_positive"  # a hard-blocking flag actually fired
    DEGRADED = "degraded"  # a guard timed out / was unavailable under fail-closed
    NONE = "none"


def flag_enabled(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "on", "yes"}


def flag_to_layer(flag: str) -> Layer | None:
    if (
        flag == "blocked"
        or flag.startswith("llama_guard:")
        or flag == "indirect_injection_suspected"
    ):
        return Layer.INPUT
    if flag.startswith("policy:") or flag.startswith("hallucinated:") or flag.startswith(
        "pii:"
    ):
        return Layer.OUTPUT
    if flag == "cross_tenant_blocked":
        return Layer.MEMORY
    return None


# Exact flags and prefixes that should hard-stop a request.
BLOCKING_FLAGS: tuple[str, ...] = (
    "blocked",
    "policy:",
    "hallucinated:",
    "cross_tenant_blocked",
)


# Availability-failure markers (guard timed out / dependency unavailable). These
# are NON-blocking by default (fail-open: favor availability). When
# GUARDRAIL_FAIL_CLOSED=on, the Supervisor treats their presence as a block
# (fail-closed: favor safety). Kept separate from BLOCKING_FLAGS so the eval /
# ablation scoring (which measures real catches) is unaffected.
DEGRADED_FLAGS: tuple[str, ...] = (
    "llama_guard_timeout",
    "llama_guard_unavailable",
    "presidio_unavailable",
    "policy_judge_timeout",
    "policy_judge_unavailable",
)


def has_degraded_flag(flags: Iterable[str]) -> bool:
    flagset = set(flags)
    return any(marker in flagset for marker in DEGRADED_FLAGS)


_FALSY = {"0", "false", "off", "no"}


def resolve_fail_closed(fail_closed: object, env_profile: object = "demo") -> bool:
    """Resolve the effective fail-closed decision.

    `"on"`/`"off"` are explicit overrides; anything else (`"auto"`/unset) follows
    the profile — `production` → fail-closed, `demo` → fail-open.
    """
    value = str(fail_closed).strip().lower()
    if flag_enabled(value):
        return True
    if value in _FALSY:
        return False
    return str(env_profile).strip().lower() == "production"


def block_kind(flags: Iterable[str], *, fail_closed: bool) -> BlockKind:
    """Classify why (if at all) these flags block the request."""
    normalized = list(flags)
    if blocked_by_flags(normalized):
        return BlockKind.TRUE_POSITIVE
    if fail_closed and has_degraded_flag(normalized):
        return BlockKind.DEGRADED
    return BlockKind.NONE


def is_blocking_flag(flag: str) -> bool:
    for marker in BLOCKING_FLAGS:
        if marker.endswith(":"):
            if flag.startswith(marker):
                return True
            continue
        if flag == marker:
            return True
    return False


def first_blocking_flag(flags: Iterable[str]) -> str | None:
    for flag in flags:
        if is_blocking_flag(flag):
            return flag
    return None


def blocked_by_flags(flags: Iterable[str]) -> bool:
    return first_blocking_flag(flags) is not None


def blocking_layer(flags: Iterable[str]) -> Layer | None:
    flag = first_blocking_flag(flags)
    if flag is None:
        return None
    return flag_to_layer(flag)


@dataclass(slots=True)
class GuardrailConfig:
    l1: bool = True
    l2: bool = True
    l3: bool = True
    l4: bool = True

    def as_env(self) -> dict[str, str]:
        return {
            "GUARDRAIL_L1": "on" if self.l1 else "off",
            "GUARDRAIL_L2": "on" if self.l2 else "off",
            "GUARDRAIL_L3": "on" if self.l3 else "off",
            "GUARDRAIL_L4": "on" if self.l4 else "off",
        }


def guardrail_profiles() -> dict[str, GuardrailConfig]:
    return {
        "baseline": GuardrailConfig(l1=True, l2=True, l3=True, l4=True),
        "l1_only": GuardrailConfig(l1=True, l2=False, l3=False, l4=False),
        "l3_only": GuardrailConfig(l1=False, l2=False, l3=True, l4=False),
        "l4_only": GuardrailConfig(l1=False, l2=False, l3=False, l4=True),
        "ablate_l1": GuardrailConfig(l1=False, l2=True, l3=True, l4=True),
        "ablate_l3": GuardrailConfig(l1=True, l2=True, l3=False, l4=True),
        "ablate_l4": GuardrailConfig(l1=True, l2=True, l3=True, l4=False),
        "all_off": GuardrailConfig(l1=False, l2=False, l3=False, l4=False),
    }


@dataclass(slots=True)
class GuardrailReport:
    flags: list[str]
    blocked: bool
    blocking_flag: str | None
    blocking_layer: Layer | None
    outcome: GuardrailOutcome

    @classmethod
    def from_flags(cls, flags: Iterable[str]) -> GuardrailReport:
        normalized = sorted(set(flags))
        blocking_flag = first_blocking_flag(normalized)
        blocked = blocking_flag is not None
        if blocked:
            outcome = GuardrailOutcome.BLOCKED
        elif normalized:
            outcome = GuardrailOutcome.MITIGATED
        else:
            outcome = GuardrailOutcome.CLEAN
        return cls(
            flags=normalized,
            blocked=blocked,
            blocking_flag=blocking_flag,
            blocking_layer=blocking_layer(normalized),
            outcome=outcome,
        )
