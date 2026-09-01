"""Post-retrieval guardrail for indirect injection and KB poisoning scans.

This closes an M5 gap: L1 `input_filter` scans only user input, not RAG
retrieval results. Once real hybrid retrieval is enabled, poisoned KB documents
could bypass user-input detection, so this layer scans the retrieval output.

Prefer reuse over custom logic: use L1
`InputGuardrail.INDIRECT_INJECTION_PATTERNS` rather than defining another rule
set. This pure function makes unit testing straightforward and lets M7/M8
include the number of retrieved poisoned documents in their metrics.
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
    """Scan retrieved documents and quarantine injection-pattern matches.

    Quarantined documents do not enter the LLM context. Return filtered safe
    documents, quarantined document IDs, and attribution flags.
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
