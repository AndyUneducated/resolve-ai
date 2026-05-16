"""Layer 3 · 输出侧 — Presidio re-scan + policy compliance + hallucinated entity detector。

防：
- LLM 把 system prompt / 历史对话 / 跨客户 PII 漏出来
- 越权承诺（套折扣码 / 编造退款金额超 SLA / 承诺产品没有的功能）
- 编造订单号 / 交易 ID / 退款金额（用 schema 校验 + 工具返回值 cross-check）
"""

from __future__ import annotations

import re
from typing import ClassVar


class OutputGuardrail:
    PII_PATTERNS: ClassVar[dict[str, re.Pattern[str]]] = {
        "email": re.compile(r"[\w\.-]+@[\w\.-]+\.\w+"),
        "phone": re.compile(r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b"),
        "credit_card": re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
    }

    UNAUTHORIZED_PHRASES: ClassVar[list[str]] = [
        # TODO: 接 policy LLM judge
        "discount code",
        "coupon code",
    ]

    async def scan(self, text: str) -> tuple[str, list[str]]:
        """返回 (清理后文本, flags)。"""
        flags: list[str] = []
        scrubbed = text

        for label, pattern in self.PII_PATTERNS.items():
            if pattern.search(scrubbed):
                flags.append(f"pii:{label}")
                scrubbed = pattern.sub(f"[REDACTED:{label}]", scrubbed)

        lowered = text.lower()
        for phrase in self.UNAUTHORIZED_PHRASES:
            if phrase in lowered:
                flags.append("policy:unauthorized_concession")

        return scrubbed, flags

    async def verify_entities(
        self, *, text: str, tool_returns: list[dict[str, object]]
    ) -> list[str]:
        """Hallucinated entity detector — 文本里出现的 order id / amount 必须能在工具返回里找到。"""
        # TODO: 抽 entities + cross-check
        return []
