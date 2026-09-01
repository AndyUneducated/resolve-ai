"""Layer 1 · Input side — Llama Guard, indirect-injection detection, and Presidio PII redaction.

Protects against:
- jailbreaks, hate speech, and illegal content (Llama Guard)
- malicious instructions embedded in customer tickets
- input-side PII leaking into the LLM context
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import ClassVar

from langchain_core.messages import HumanMessage, SystemMessage

from resolveai_api.config import get_settings
from resolveai_api.guardrails.attribution import flag_enabled
from resolveai_api.guardrails.presidio import drop_ignored_entities, get_presidio

logger = logging.getLogger(__name__)


@dataclass
class LlamaGuardDecision:
    allowed: bool
    categories: list[str]


class LlamaGuardClient:
    """Run local Ollama-hosted Llama Guard and parse safe/unsafe verdict."""

    def __init__(self, *, model: str, base_url: str, timeout_ms: int) -> None:
        from langchain_ollama import ChatOllama

        self._timeout_s = max(timeout_ms, 100) / 1000.0
        self._llm = ChatOllama(model=model, base_url=base_url, temperature=0)

    async def classify(self, text: str) -> LlamaGuardDecision:
        messages = [
            SystemMessage(
                content=(
                    "You are a safety classifier. Respond in one line only: "
                    "'safe' or 'unsafe <category_codes>'."
                )
            ),
            HumanMessage(content=text),
        ]
        reply = await asyncio.wait_for(self._llm.ainvoke(messages), timeout=self._timeout_s)
        content = str(reply.content).strip()
        lowered = content.lower()
        if not lowered.startswith("unsafe"):
            return LlamaGuardDecision(allowed=True, categories=[])
        categories = [m.group(0).upper() for m in re.finditer(r"s\d+", lowered)]
        return LlamaGuardDecision(allowed=False, categories=categories)


class InputGuardrail:
    """Input guardrails: injection heuristic + Llama Guard + Presidio scrub.

    Heavy dependencies (Llama Guard client, Presidio analyzer) are lazy — they
    are only constructed on first use when L1 is enabled, so disabling the layer
    avoids loading spaCy entirely.
    """

    INDIRECT_INJECTION_PATTERNS: ClassVar[list[str]] = [
        "ignore previous",
        "ignore the above",
        "system prompt",
        "you ignore",
    ]

    def __init__(self) -> None:
        settings = get_settings()
        self._enabled = flag_enabled(getattr(settings, "guardrail_l1", "on"))
        self._language = str(getattr(settings, "presidio_language", "en"))
        self._llama_guard_model = str(getattr(settings, "llama_guard_model", "llama-guard3:8b"))
        self._llama_guard_timeout_ms = int(getattr(settings, "llama_guard_timeout_ms", 2000))
        self._ollama_base_url = settings.ollama_base_url
        self._llama_guard: LlamaGuardClient | None = None

    def _get_llama_guard(self) -> LlamaGuardClient:
        if self._llama_guard is None:
            self._llama_guard = LlamaGuardClient(
                model=self._llama_guard_model,
                base_url=self._ollama_base_url,
                timeout_ms=self._llama_guard_timeout_ms,
            )
        return self._llama_guard

    def _scrub(self, text: str) -> tuple[str, list[str]]:
        bundle = get_presidio()
        results = drop_ignored_entities(
            bundle.analyzer.analyze(text=text, language=self._language)
        )
        if not results:
            return text, []
        anonymized = bundle.anonymizer.anonymize(text=text, analyzer_results=results)
        flags = [f"pii:{result.entity_type.lower()}" for result in results]
        return anonymized.text, sorted(set(flags))

    async def scan_and_redact(self, text: str) -> tuple[str, list[str]]:
        """Return (redacted text, flags); callers must abort when flags contains 'blocked'."""
        if not self._enabled:
            return text, []

        flags: list[str] = []

        lowered = text.lower()
        if any(p in lowered for p in self.INDIRECT_INJECTION_PATTERNS):
            flags.append("indirect_injection_suspected")

        try:
            decision = await self._get_llama_guard().classify(text)
            if not decision.allowed:
                flags.append("blocked")
                flags.extend(f"llama_guard:{category}" for category in decision.categories)
        except TimeoutError:
            flags.append("llama_guard_timeout")
        except Exception:
            logger.exception("llama_guard_classification_failed")
            flags.append("llama_guard_unavailable")

        try:
            scrubbed, pii_flags = self._scrub(text)
            flags.extend(pii_flags)
        except Exception:
            logger.exception("presidio_scrub_failed")
            scrubbed = text
            flags.append("presidio_unavailable")

        return scrubbed, sorted(set(flags))
