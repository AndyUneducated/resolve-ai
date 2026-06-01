"""Presidio entity-exclusion (redaction scope) — pure, hermetic (no spaCy load).

Covers the M-fix that narrows what counts as PII: `DATE_TIME` is excluded by
default so benign "yesterday"/"last month" stop producing noisy `pii:date_time`
flags. This only changes redaction scope, never a blocking decision.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from resolveai_api.config import get_settings
from resolveai_api.guardrails.presidio import drop_ignored_entities


@dataclass
class _FakeResult:
    """Stand-in for a presidio RecognizerResult (only entity_type is read)."""

    entity_type: str


def _types(results: list[_FakeResult]) -> list[str]:
    return [r.entity_type for r in results]


def test_default_excludes_date_time_keeps_sensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PRESIDIO_IGNORED_ENTITIES", raising=False)
    get_settings.cache_clear()
    results = [
        _FakeResult("DATE_TIME"),
        _FakeResult("EMAIL_ADDRESS"),
        _FakeResult("CREDIT_CARD"),
    ]
    kept = drop_ignored_entities(results)
    assert _types(kept) == ["EMAIL_ADDRESS", "CREDIT_CARD"]


def test_match_is_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    # Presidio normally upper-cases entity types, but the filter must not rely on
    # the analyzer's casing — it upper-cases both sides before comparing.
    monkeypatch.delenv("PRESIDIO_IGNORED_ENTITIES", raising=False)
    get_settings.cache_clear()
    kept = drop_ignored_entities([_FakeResult("date_time"), _FakeResult("Email_Address")])
    assert _types(kept) == ["Email_Address"]


def test_empty_config_redacts_every_type(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRESIDIO_IGNORED_ENTITIES", "")
    get_settings.cache_clear()
    assert get_settings().presidio_ignored_entities_set == set()
    results = [_FakeResult("DATE_TIME"), _FakeResult("EMAIL_ADDRESS")]
    # Empty config → nothing is filtered out (every detected type still redacted).
    assert _types(drop_ignored_entities(results)) == ["DATE_TIME", "EMAIL_ADDRESS"]


def test_multiple_ignored_entities_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRESIDIO_IGNORED_ENTITIES", "date_time, url ,NRP")
    get_settings.cache_clear()
    assert get_settings().presidio_ignored_entities_set == {"DATE_TIME", "URL", "NRP"}
    kept = drop_ignored_entities(
        [
            _FakeResult("DATE_TIME"),
            _FakeResult("URL"),
            _FakeResult("NRP"),
            _FakeResult("PHONE_NUMBER"),
        ]
    )
    assert _types(kept) == ["PHONE_NUMBER"]


def test_empty_input_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PRESIDIO_IGNORED_ENTITIES", raising=False)
    get_settings.cache_clear()
    assert drop_ignored_entities([]) == []
