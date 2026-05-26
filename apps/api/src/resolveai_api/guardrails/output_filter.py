"""Layer 3 · 输出侧 — Presidio re-scan + policy compliance + hallucinated entity detector。

防：
- LLM 把 system prompt / 历史对话 / 跨客户 PII 漏出来
- 越权承诺（套折扣码 / 编造退款金额超 SLA / 承诺产品没有的功能）
- 编造订单号 / 交易 ID / 退款金额（用 schema 校验 + 工具返回值 cross-check）
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import ClassVar

from pydantic import BaseModel, Field

from resolveai_api.config import get_settings
from resolveai_api.core.llm import make_structured_llm
from resolveai_api.guardrails.presidio import get_presidio

logger = logging.getLogger(__name__)


def _flag_enabled(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "on", "yes"}


class PolicyVerdict(BaseModel):
    violations: list[str] = Field(default_factory=list)
    reason: str = ""


class PolicyJudge:
    """Structured policy compliance judge for model outputs.

    Heavy LLM client is built lazily so disabling L3 incurs no cost.
    """

    def __init__(self, *, timeout_ms: int) -> None:
        self._timeout_s = max(timeout_ms, 100) / 1000.0
        self._runnable = None  # built lazily on first judge() call

    def _build_runnable(self):
        settings = get_settings()
        if settings.llm_backend == "ollama":
            from langchain_ollama import ChatOllama

            return ChatOllama(
                model=settings.policy_judge_model,
                base_url=settings.ollama_base_url,
                temperature=0,
            ).with_structured_output(PolicyVerdict)
        return make_structured_llm("vertical", PolicyVerdict)

    async def judge(self, text: str) -> PolicyVerdict:
        if self._runnable is None:
            self._runnable = self._build_runnable()
        prompt = (
            "Classify whether the assistant response violates policy.\n"
            "Return violations from this fixed set only: "
            "unauthorized_concession,off_topic_speculation,regulated_advice.\n"
            "If none, return an empty list.\n\n"
            f"assistant_response:\n{text}"
        )
        result = await asyncio.wait_for(self._runnable.ainvoke(prompt), timeout=self._timeout_s)
        return result if isinstance(result, PolicyVerdict) else PolicyVerdict()


class OutputGuardrail:
    """Output guardrails: Presidio rescan + policy LLM judge + hallucinated entity check.

    All heavy resources are lazy — when L3 is off, no Presidio / LLM is loaded.
    """

    ENTITY_PATTERNS: ClassVar[dict[str, re.Pattern[str]]] = {
        "charge_id": re.compile(r"\bch_[A-Za-z0-9]+\b"),
        "customer_id": re.compile(r"\bcus_[A-Za-z0-9]+\b"),
        "ticket_id": re.compile(r"\btkt_\d+\b"),
        "usd_amount": re.compile(r"\$[\d,]+(?:\.\d{2})?"),
    }

    def __init__(self) -> None:
        settings = get_settings()
        self._enabled = _flag_enabled(getattr(settings, "guardrail_l3", "on"))
        self._language = str(getattr(settings, "presidio_language", "en"))
        self._judge = PolicyJudge(
            timeout_ms=int(getattr(settings, "policy_judge_timeout_ms", 1500))
        )

    async def scan(
        self, text: str, tool_calls: list[dict[str, object]] | None = None
    ) -> tuple[str, list[str]]:
        """返回 (清理后文本, flags)。"""
        if not self._enabled:
            return text, []

        flags: list[str] = []
        scrubbed = text

        try:
            bundle = get_presidio()
            results = bundle.analyzer.analyze(text=text, language=self._language)
            if results:
                scrubbed = bundle.anonymizer.anonymize(
                    text=text, analyzer_results=results
                ).text
                flags.extend(f"pii:{result.entity_type.lower()}" for result in results)
        except Exception:
            logger.exception("output_presidio_scan_failed")
            flags.append("presidio_unavailable")

        try:
            verdict = await self._judge.judge(scrubbed)
            flags.extend(f"policy:{violation}" for violation in verdict.violations)
        except TimeoutError:
            flags.append("policy_judge_timeout")
        except Exception:
            logger.exception("policy_judge_failed")
            flags.append("policy_judge_unavailable")

        # Run hallucinated-entity check on the *original* text — Presidio may
        # have redacted $ amounts or ids that we need for the cross-check.
        flags.extend(
            await self.verify_entities(text=text, tool_returns=tool_calls or [])
        )
        return scrubbed, sorted(set(flags))

    async def verify_entities(
        self, *, text: str, tool_returns: list[dict[str, object]]
    ) -> list[str]:
        """Hallucinated entity detector — 文本里出现的 order id / amount 必须能在工具返回里找到。"""
        serialized = json.dumps(tool_returns, default=str)
        misses: list[str] = []
        for pattern in self.ENTITY_PATTERNS.values():
            for match in pattern.findall(text):
                if match not in serialized:
                    misses.append(f"hallucinated:{match}")
        return sorted(set(misses))
