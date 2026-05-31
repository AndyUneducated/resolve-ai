"""检索后 guardrail — 对召回 chunk 做间接注入 / KB 投毒扫描。

补 M5 缺口：L1 `input_filter` 只扫用户输入，不扫 RAG 召回内容。真实 hybrid
检索上线后，被投毒的 KB 文档可能绕过"用户输入"检测，因此在检索出口加这一层。

调库优先：复用 L1 `InputGuardrail.INDIRECT_INJECTION_PATTERNS`，不另立一套规则。
纯函数、无外呼，便于单测，也便于 M7/M8 把"被污染文档命中数"纳入指标。
"""

from __future__ import annotations

from dataclasses import dataclass

from resolveai_api.guardrails.input_filter import InputGuardrail
from resolveai_api.retrieval.types import RetrievedDoc


@dataclass
class ChunkScanResult:
    safe_docs: list[RetrievedDoc]
    quarantined_ids: list[int]
    flags: list[str]


def scan_chunks(docs: list[RetrievedDoc]) -> ChunkScanResult:
    """扫描召回文档；命中注入模式的文档被隔离（不进 LLM context）。

    返回过滤后的 safe_docs + 被隔离 doc id + 标记（供 guardrail_flags 归因）。
    """
    patterns = [p.lower() for p in InputGuardrail.INDIRECT_INJECTION_PATTERNS]
    safe: list[RetrievedDoc] = []
    quarantined: list[int] = []
    flags: list[str] = []

    for doc in docs:
        haystack = f"{doc.title}\n{doc.content}".lower()
        if any(pattern in haystack for pattern in patterns):
            quarantined.append(doc.id)
            flags.append(f"kb_injection_suspected:doc_{doc.id}")
        else:
            safe.append(doc)

    return ChunkScanResult(
        safe_docs=safe,
        quarantined_ids=quarantined,
        flags=sorted(set(flags)),
    )
