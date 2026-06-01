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


def drop_ignored_entities(results: list) -> list:
    """Filter out entity types configured as non-sensitive (redaction scope only).

    Driven by `settings.presidio_ignored_entities` (default `DATE_TIME`). This only
    narrows what gets redacted / flagged as PII; it does NOT touch any blocking
    decision (`pii:*` flags are not in BLOCKING_FLAGS). Keeps benign date mentions
    like "yesterday" from producing noisy `pii:date_time` flags.
    """
    from resolveai_api.config import get_settings

    ignored = get_settings().presidio_ignored_entities_set
    if not ignored:
        return results
    return [r for r in results if str(r.entity_type).upper() not in ignored]


def reset_for_tests() -> None:
    """Drop the singleton; tests that swap analyzers can call this."""
    global _bundle
    _bundle = None
