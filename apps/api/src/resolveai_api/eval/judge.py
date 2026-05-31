"""LLM-judge for auto-resolution quality (M7).

Given a ticket prompt, a human-written rubric, and the agent's final answer (plus
a short tool-call summary), the judge decides whether the ticket was actually
*resolved* and assigns a 0-1 quality score.

IMPORTANT: the judge itself spends tokens. Callers MUST run it OUTSIDE the
`core.usage.capture_run()` context so judge tokens never pollute a variant's
token / cost numbers.
"""

from __future__ import annotations

import logging

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from resolveai_api.core.llm import make_structured_llm

logger = logging.getLogger(__name__)

JUDGE_SYSTEM_PROMPT = """\
You are an impartial evaluator for an enterprise customer-support AI.

Given the customer's ticket, a resolution rubric, and the agent's final answer,
decide whether the ticket was genuinely resolved.

Scoring guidance:
- resolved=true ONLY if the answer satisfies the rubric and would not require the
  customer to ask again or a human to redo the work.
- score in [0,1]: 1.0 = fully correct and complete; 0.5 = partially helpful but
  missing a rubric requirement; 0.0 = wrong, evasive, hallucinated, or unsafe.
- Penalize fabricated amounts / IDs, unauthorized concessions, and ungrounded
  claims even if the tone is helpful.
"""


class ResolutionVerdict(BaseModel):
    """Structured judgment of a single ticket resolution."""

    resolved: bool = Field(..., description="Did the answer actually resolve the ticket?")
    score: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Resolution quality in [0,1]."
    )
    reason: str = Field(default="", description="One-sentence justification.")


class ResolutionJudge:
    """Wraps a vertical-tier structured LLM call returning a `ResolutionVerdict`."""

    async def judge(
        self,
        *,
        prompt: str,
        rubric: str,
        final_answer: str,
        tool_summary: str = "",
        blocked: bool = False,
    ) -> ResolutionVerdict:
        if blocked:
            return ResolutionVerdict(
                resolved=False,
                score=0.0,
                reason="Run was blocked by guardrails; no resolution delivered.",
            )
        if not (final_answer or "").strip():
            return ResolutionVerdict(
                resolved=False, score=0.0, reason="Empty final answer."
            )

        user = (
            f"Customer ticket:\n{prompt}\n\n"
            f"Resolution rubric:\n{rubric}\n\n"
            f"Tools the agent called: {tool_summary or '(none)'}\n\n"
            f"Agent final answer:\n{final_answer}\n\n"
            "Return your verdict."
        )
        try:
            llm = make_structured_llm("vertical", ResolutionVerdict)
            verdict = await llm.ainvoke(
                [
                    SystemMessage(content=JUDGE_SYSTEM_PROMPT),
                    HumanMessage(content=user),
                ]
            )
            if isinstance(verdict, ResolutionVerdict):
                return verdict
            return ResolutionVerdict.model_validate(verdict)
        except Exception:
            logger.exception("resolution_judge_failed")
            return ResolutionVerdict(
                resolved=False, score=0.0, reason="Judge call failed."
            )
