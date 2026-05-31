"""Technical Agent — KB-grounded answers via hybrid retrieval (M6)."""

from __future__ import annotations

from typing import Any, ClassVar
from unittest.mock import patch

import pytest
from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool
from resolveai_api.agents.state import GraphState
from resolveai_api.agents.technical import TechnicalAgent, TechnicalAnswer
from resolveai_api.core.executor import Executor
from resolveai_api.retrieval.types import RetrievalTrace, RetrievedDoc


class _FakeRetriever:
    """Stand-in for HybridRetriever — no DB / no embeddings."""

    def __init__(self, docs: list[RetrievedDoc]) -> None:
        self._docs = docs
        self.queries: list[str] = []

    async def search_with_trace(
        self, *, query: str, tenant_id: str, k: int = 5
    ) -> tuple[list[RetrievedDoc], RetrievalTrace]:
        self.queries.append(query)
        trace = RetrievalTrace(
            query=query,
            tenant_id=tenant_id,
            profile="hybrid",
            result_ids=[d.id for d in self._docs],
        )
        return self._docs, trace


class _FakeStructuredLLM:
    def __init__(self, answer: TechnicalAnswer) -> None:
        self._answer = answer

    async def ainvoke(self, _messages: Any) -> TechnicalAnswer:
        return self._answer


class _FakeHistory(BaseTool):
    name: str = "zendesk_get_ticket_history"
    description: str = "fake history"
    metadata: ClassVar[dict[str, Any]] = {
        "server": "zendesk",
        "capability": "read",
        "full_name": "zendesk.get_ticket_history",
    }

    def _run(self, *args, **kwargs):
        return []

    async def _arun(self, *args, **kwargs):
        return [{"id": "zd_010", "subject": "API 502 spikes", "status": "open"}]


def _kb_docs() -> list[RetrievedDoc]:
    return [
        RetrievedDoc(
            id=42,
            title="Resolve 502 errors",
            content="Check the load balancer and upstream timeouts.",
            score=0.9,
            metadata={"category": "technical"},
            source="rerank",
        ),
        RetrievedDoc(
            id=7,
            title="Webhook retries",
            content="Retries use exponential backoff.",
            score=0.5,
            metadata={"category": "technical"},
            source="rerank",
        ),
    ]


def _state(query: str = "Our API returns 502 errors") -> GraphState:
    return {
        "messages": [HumanMessage(content=query)],
        "customer_id": "cus_demo_001",
        "tenant_id": "demo",
        "tool_calls": [],
        "guardrail_flags": [],
    }


@pytest.mark.asyncio
async def test_technical_grounds_answer_with_doc_ids() -> None:
    retriever = _FakeRetriever(_kb_docs())
    agent = TechnicalAgent.default(tools=[], executor=Executor(), retriever=retriever)

    answer = TechnicalAnswer(answer="Check the load balancer health checks.", cited_doc_ids=[42])
    with patch(
        "resolveai_api.agents.technical.make_structured_llm",
        return_value=_FakeStructuredLLM(answer),
    ):
        result = await agent.run(_state())

    steps = [tc.get("step") for tc in result["tool_calls"]]
    assert "kb.search" in steps
    content = result["messages"][0].content
    assert "[doc 42]" in content
    assert "grounding:hallucinated_doc_id" not in result["guardrail_flags"]
    assert retriever.queries  # retriever was actually queried with the user message


@pytest.mark.asyncio
async def test_technical_flags_hallucinated_citation() -> None:
    retriever = _FakeRetriever(_kb_docs())
    agent = TechnicalAgent.default(tools=[], executor=Executor(), retriever=retriever)

    # Model cites a doc id (999) that was never retrieved.
    answer = TechnicalAnswer(answer="Do the thing per docs.", cited_doc_ids=[999])
    with patch(
        "resolveai_api.agents.technical.make_structured_llm",
        return_value=_FakeStructuredLLM(answer),
    ):
        result = await agent.run(_state())

    assert "grounding:hallucinated_doc_id" in result["guardrail_flags"]
    # Hallucinated id must not be surfaced; answer is anchored to a real retrieved doc.
    assert "[doc 999]" not in result["messages"][0].content


@pytest.mark.asyncio
async def test_technical_escalates_when_no_kb_context() -> None:
    retriever = _FakeRetriever([])  # empty corpus / DB miss
    agent = TechnicalAgent.default(tools=[], executor=Executor(), retriever=retriever)
    result = await agent.run(_state())

    assert "grounding:no_kb_context" in result["guardrail_flags"]
    assert "Escalating" in result["messages"][0].content


@pytest.mark.asyncio
async def test_technical_still_fetches_ticket_history() -> None:
    retriever = _FakeRetriever([])
    agent = TechnicalAgent.default(
        tools=[_FakeHistory()], executor=Executor(), retriever=retriever
    )
    result = await agent.run(_state())
    steps = [tc.get("step") for tc in result["tool_calls"]]
    assert "zendesk.get_ticket_history" in steps
