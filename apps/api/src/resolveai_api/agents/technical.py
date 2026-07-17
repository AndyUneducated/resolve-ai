"""Technical Agent — KB-grounded answers via hybrid retrieval (M6).

M6 范围：接入 [`retrieval.HybridRetriever`](../retrieval/hybrid.py) 的 BM25 + dense
+ RRF + reranker，先检索内部 KB / FAQ / runbook，再用 LLM 生成**带 doc id 引用**的
回复。仍保留 M3 的 Zendesk 历史拉取作为补充 context。

Grounding 契约（供 M7/M8 评测 + M5 安全归因复用）：
- 回复引用的 doc id 必须 ⊆ 当次检索结果集；越界的视为幻觉并被剔除 + 打标。
- 召回 chunk 先过 `retrieval_filter.scan_chunks`（间接注入 / KB 投毒），命中即隔离。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from resolveai_api.agents.base import AgentConfig, BaseAgent, find_tool
from resolveai_api.agents.state import GraphState
from resolveai_api.core.llm import make_structured_llm
from resolveai_api.guardrails.retrieval_filter import scan_chunks
from resolveai_api.retrieval.types import RetrievedDoc

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are the Technical Agent. You handle bug reports, feature questions, and configuration help.

Process (Plan-and-Execute):
1. Search internal KB / FAQ via hybrid retrieval first.
2. If reproducible from logs / past tickets, propose a fix.
3. If not reproducible OR SLA-impacting → handoff to escalation.

Hard rules:
- NEVER promise features that are not in the product changelog.
- ALWAYS ground instructions in the retrieved KB documents and cite their doc IDs.
- ONLY cite doc IDs that appear in the provided KB context; never invent IDs.
"""

ANSWER_SYSTEM_PROMPT = """\
You are the Technical Agent. Using ONLY the retrieved KB documents below, write a
concise, actionable answer for the customer.

Rules:
- Ground every instruction in the KB context.
- Put the doc IDs you actually relied on in `cited_doc_ids` (integers only).
- NEVER cite a doc ID that is not present in the KB context.
- If the KB context is insufficient, say so and set escalate=true.
"""

TOOL_WHITELIST = [
    "zendesk.get_ticket_history",
    "zendesk.update_ticket",
    "intercom.get_conversation",
    "kb.search",  # served in-process by HybridRetriever (no MCP needed)
]


class TechnicalAnswer(BaseModel):
    """Grounded answer with explicit citations for verification."""

    answer: str = Field(..., description="What to say to the customer.")
    cited_doc_ids: list[int] = Field(
        default_factory=list, description="KB doc IDs actually relied upon."
    )
    escalate: bool = Field(default=False)


def _latest_user_query(messages: list[BaseMessage], ticket_summary: dict[str, Any]) -> str:
    for msg in reversed(messages or []):
        if isinstance(msg, HumanMessage) and isinstance(msg.content, str):
            return msg.content
    entities = ticket_summary.get("entities") if ticket_summary else None
    if isinstance(entities, dict) and entities.get("subject"):
        return str(entities["subject"])
    return str((ticket_summary or {}).get("intent") or "")


def _format_kb_context(docs: list[RetrievedDoc]) -> str:
    if not docs:
        return "(no KB documents retrieved)"
    lines = []
    for doc in docs:
        excerpt = doc.content.strip().replace("\n", " ")
        if len(excerpt) > 500:
            excerpt = excerpt[:500] + "…"
        lines.append(f"[doc {doc.id}] {doc.title}: {excerpt}")
    return "\n".join(lines)


class TechnicalAgent(BaseAgent):
    """KB-grounded technical agent. Retriever is injectable (None → load default)."""

    def __init__(
        self,
        *,
        retriever: Any | None = None,
        handoff: str = "structured",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._retriever = retriever
        self._retriever_resolved = retriever is not None
        self._handoff = handoff

    @classmethod
    def default(cls, *, handoff: str = "structured", **kwargs: Any) -> TechnicalAgent:
        from resolveai_api.config import get_settings

        settings = get_settings()
        config = AgentConfig(
            name="technical",
            model=settings.vertical_model,
            system_prompt=SYSTEM_PROMPT,
            tool_whitelist=list(TOOL_WHITELIST),
        )
        return cls(config=config, handoff=handoff, **kwargs)

    def _get_retriever(self) -> Any | None:
        if not self._retriever_resolved:
            try:
                from resolveai_api.retrieval import get_retriever

                self._retriever = get_retriever()
            except Exception:
                logger.exception("technical_retriever_init_failed")
                self._retriever = None
            self._retriever_resolved = True
        return self._retriever

    async def _retrieve_kb(
        self, *, query: str, tenant_id: str
    ) -> tuple[list[RetrievedDoc], dict[str, Any], list[str]]:
        """Return (safe_docs, kb_search_tool_call, guardrail_flags)."""
        retriever = self._get_retriever()
        if retriever is None or not query.strip():
            return [], {}, []
        from resolveai_api.config import get_settings

        top_k = get_settings().retrieval_top_k
        try:
            docs, trace = await retriever.search_with_trace(
                query=query, tenant_id=tenant_id, k=top_k
            )
        except Exception as exc:
            logger.exception("technical_kb_search_failed")
            return [], {"step": "kb.search", "error": str(exc)}, []

        scan = scan_chunks(docs)
        tool_call = {
            "step": "kb.search",
            "observation": json.dumps(
                {
                    "doc_ids": [d.id for d in scan.safe_docs],
                    "quarantined_ids": scan.quarantined_ids,
                    "trace": trace.as_dict(),
                },
                default=str,
            )[:1000],
        }
        return scan.safe_docs, tool_call, scan.flags

    async def run(self, state: GraphState) -> GraphState:
        messages = list(state.get("messages") or [])
        ticket_summary = dict(state.get("ticket_summary") or {})
        from resolveai_api.config import get_settings

        tenant_id = (
            state.get("tenant_id")
            or ticket_summary.get("tenant_id")
            or get_settings().default_tenant_id
        )
        customer_id = state.get("customer_id") or ""
        tool_calls = list(state.get("tool_calls") or [])
        guardrail_flags = list(state.get("guardrail_flags") or [])
        context_lines: list[str] = []

        query = _latest_user_query(messages, ticket_summary)

        # ---- 1. KB hybrid retrieval (grounding source) ----
        kb_docs, kb_tool_call, kb_flags = await self._retrieve_kb(
            query=query, tenant_id=str(tenant_id)
        )
        if kb_tool_call:
            tool_calls.append(kb_tool_call)
        guardrail_flags.extend(kb_flags)

        # ---- 2. Zendesk history (supplementary context, M3 behavior retained) ----
        history_tool = find_tool(self.tools, "zendesk.get_ticket_history")
        if history_tool is not None and customer_id:
            try:
                result = await self.executor.call_tool(
                    tool=history_tool,
                    args={"customer_id": customer_id},
                    whitelist=self.config.tool_whitelist,
                )
                tickets = result.output if isinstance(result.output, list) else []
                tool_calls.append(
                    {
                        "step": "zendesk.get_ticket_history",
                        "observation": json.dumps(tickets, default=str)[:1000],
                    }
                )
                if tickets:
                    titles = ", ".join(str(t.get("subject", "")) for t in tickets[:3])
                    context_lines.append(
                        f"Found {len(tickets)} prior tickets for {customer_id}: {titles}"
                    )
            except Exception as exc:  # best effort context fetch
                tool_calls.append(
                    {"step": "zendesk.get_ticket_history", "error": str(exc)}
                )

        # Variant B (full_transcript handoff): replay the whole conversation as
        # supplementary context instead of relying on the structured summary.
        if self._handoff == "full_transcript":
            transcript = "\n".join(
                m.content
                for m in messages
                if isinstance(m, BaseMessage) and isinstance(m.content, str) and m.content
            )
            if transcript:
                context_lines.append(f"Full conversation transcript:\n{transcript}")

        # ---- 3. Grounded answer generation + citation verification ----
        message, answer_flags, escalate = await self._generate_answer(
            query=query, kb_docs=kb_docs, history_lines=context_lines
        )
        guardrail_flags.extend(answer_flags)

        return {
            **state,
            "messages": [AIMessage(content=message)],
            "tool_calls": tool_calls,
            "guardrail_flags": sorted(set(guardrail_flags)),
            "escalate": escalate,
        }

    async def _generate_answer(
        self, *, query: str, kb_docs: list[RetrievedDoc], history_lines: list[str]
    ) -> tuple[str, list[str], bool]:
        """Produce a grounded reply; verify citations ⊆ retrieved doc ids.

        Returns `(message, flags, escalate)`; `escalate=True` asks the Supervisor
        to route to the escalation node (real handoff) after this reply.
        """
        if not kb_docs:
            # No KB context (DB down / empty corpus): degrade to a safe handoff note.
            note = (
                "I couldn't find a matching KB article for this issue. "
                "Escalating to a human engineer with the available context."
            )
            if history_lines:
                note += "\n" + "\n".join(f"- {line}" for line in history_lines)
            return note, ["grounding:no_kb_context"], True

        kb_context = _format_kb_context(kb_docs)
        retrieved_ids = {d.id for d in kb_docs}
        prompt = (
            f"Customer question:\n{query}\n\n"
            f"KB context:\n{kb_context}\n\n"
        )
        if history_lines:
            prompt += "Supplementary history:\n" + "\n".join(history_lines) + "\n\n"
        prompt += "Write the grounded answer now."

        flags: list[str] = []
        try:
            llm = make_structured_llm("vertical", TechnicalAnswer)
            answer = await llm.ainvoke(
                [SystemMessage(content=ANSWER_SYSTEM_PROMPT), HumanMessage(content=prompt)]
            )
            if not isinstance(answer, TechnicalAnswer):
                answer = TechnicalAnswer.model_validate(answer)
        except Exception:
            logger.exception("technical_answer_generation_failed")
            # Deterministic fallback: cite the top retrieved docs.
            cited = ", ".join(f"[doc {d.id}] {d.title}" for d in kb_docs[:3])
            return (
                f"Based on our knowledge base ({cited}), here are the relevant "
                "troubleshooting steps. Please follow the cited articles.",
                ["grounding:llm_unavailable"],
                False,
            )

        # Verify citations: drop any hallucinated doc id.
        verified = [doc_id for doc_id in answer.cited_doc_ids if doc_id in retrieved_ids]
        hallucinated = [doc_id for doc_id in answer.cited_doc_ids if doc_id not in retrieved_ids]
        if hallucinated:
            flags.append("grounding:hallucinated_doc_id")
        if not verified:
            # Model failed to cite a real doc — anchor to top retrieved doc.
            verified = [kb_docs[0].id]
            flags.append("grounding:uncited_answer")

        citation_str = ", ".join(f"[doc {doc_id}]" for doc_id in verified)
        body = answer.answer.strip()
        text = f"{body}\n\nSources: {citation_str}"
        return text, flags, bool(answer.escalate)
