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
