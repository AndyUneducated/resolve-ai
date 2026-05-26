"""Shared Presidio singleton — lazy-loaded, reused by L1 input + L3 output."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from presidio_analyzer import AnalyzerEngine
    from presidio_anonymizer import AnonymizerEngine


@dataclass
class PresidioBundle:
    analyzer: AnalyzerEngine
    anonymizer: AnonymizerEngine


_bundle: PresidioBundle | None = None
_bundle_lock = Lock()


def get_presidio() -> PresidioBundle:
    """Lazy-build a single (AnalyzerEngine, AnonymizerEngine) pair for the process."""
    global _bundle
    if _bundle is not None:
        return _bundle
    with _bundle_lock:
        if _bundle is None:
            from presidio_analyzer import AnalyzerEngine
            from presidio_anonymizer import AnonymizerEngine

            _bundle = PresidioBundle(
                analyzer=AnalyzerEngine(),
                anonymizer=AnonymizerEngine(),
            )
    return _bundle


def reset_for_tests() -> None:
    """Drop the singleton; tests that swap analyzers can call this."""
    global _bundle
    _bundle = None
