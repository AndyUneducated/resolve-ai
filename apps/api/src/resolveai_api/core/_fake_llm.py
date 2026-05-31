"""Deterministic, zero-latency LLM stand-ins for load testing (M8 chaos).

`LLM_BACKEND=fake` swaps the real Ollama/Anthropic clients for these canned
implementations. They produce schema-valid structured outputs and a plausible
final answer without any network call, so `scripts/chaos_load.py` can measure
the *framework's* concurrency overhead (LangGraph orchestration, guardrails,
checkpointer, executor) in isolation from real model latency.

Two surfaces mirror `core/llm.py`:
- `FakeChatModel`  — used by `make_llm()` (executor / ReAct nodes). Returns a
  canned `AIMessage` with no tool calls so graphs terminate quickly.
- `FakeStructuredRunnable` — used by `make_structured_llm()`. Returns a canned
  instance of whatever Pydantic schema is requested, routing triage by keyword
  so billing / technical / escalation paths all get exercised under load.
"""

from __future__ import annotations

from typing import Any

from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult

_CANNED_ANSWER = "Refund of the disputed charge has been processed. Anything else I can help with?"

# Modeled token usage so M7 accounting (`core/usage.py`) still produces non-zero
# numbers when the fake backend is active.
_FAKE_USAGE = {"input_tokens": 48, "output_tokens": 24, "total_tokens": 72}


def _result(content: str) -> ChatResult:
    message = AIMessage(content=content, usage_metadata=dict(_FAKE_USAGE))
    return ChatResult(generations=[ChatGeneration(message=message)])


class FakeChatModel(BaseChatModel):
    """A chat model that instantly returns a fixed answer (no tool calls)."""

    canned: str = _CANNED_ANSWER

    @property
    def _llm_type(self) -> str:
        return "fake"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return _result(self.canned)

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return _result(self.canned)

    def bind_tools(self, tools: Any, **kwargs: Any) -> FakeChatModel:
        # Fake model never emits tool calls; binding is a no-op so executor /
        # ReAct nodes fall through to their "no-op" / final-answer branch fast.
        return self


def _text_of(value: Any) -> str:
    """Extract the *user* text from a structured-LLM input.

    For a message list we only join Human messages, never system prompts —
    otherwise keyword routing would key off prompt boilerplate (e.g. the triage
    system prompt literally lists "escalation").
    """
    if isinstance(value, str):
        return value
    if isinstance(value, HumanMessage):
        return value.content if isinstance(value.content, str) else str(value.content)
    if isinstance(value, BaseMessage):
        return ""  # system / AI / tool messages don't carry the user's request
    if isinstance(value, (list, tuple)):
        humans = [_text_of(v) for v in value]
        joined = "\n".join(t for t in humans if t)
        return joined or " ".join(
            str(getattr(v, "content", v)) for v in value if isinstance(v, str)
        )
    return str(value or "")


_ESCALATION_HINTS = ("escalat", "fraud", "lawsuit", "chargeback", "manager", "supervisor")
_TECHNICAL_HINTS = (
    "error",
    "bug",
    "crash",
    "login",
    "config",
    "api",
    "integration",
    "install",
    "setup",
    "how do i",
    "not working",
)


def _route_intent(text: str) -> str:
    lowered = text.lower()
    if any(hint in lowered for hint in _ESCALATION_HINTS):
        return "escalation"
    if any(hint in lowered for hint in _TECHNICAL_HINTS):
        return "technical"
    return "billing"


def _canned_kwargs(schema_name: str, text: str) -> dict[str, Any] | None:
    """Return constructor kwargs for known schemas, else None for generic build."""
    if schema_name == "TriageOutput":
        return {"intent": _route_intent(text), "entities": {}, "confidence": 0.95}
    if schema_name == "Plan":
        return {"steps": ["look up the charge", "issue the refund"]}
    if schema_name == "Replan":
        return {
            "plan": None,
            "response": {"final_answer": _CANNED_ANSWER, "escalate": False},
        }
    if schema_name == "Response":
        return {"final_answer": _CANNED_ANSWER, "escalate": False}
    if schema_name == "TechnicalAnswer":
        return {
            "answer": "Follow the documented troubleshooting steps below.",
            "cited_doc_ids": [],
            "escalate": False,
        }
    if schema_name == "PolicyVerdict":
        return {"violations": [], "reason": ""}
    if schema_name == "ResolutionVerdict":
        return {"resolved": True, "score": 1.0, "reason": "fake backend auto-pass"}
    return None


def _generic_instance(schema: type) -> Any:
    """Best-effort construction of an arbitrary Pydantic schema."""
    try:
        return schema()
    except Exception:
        pass
    kwargs: dict[str, Any] = {}
    for name, model_field in getattr(schema, "model_fields", {}).items():
        if model_field.is_required():
            annotation = getattr(model_field, "annotation", None)
            kwargs[name] = _dummy_for(annotation)
    return schema(**kwargs)


def _dummy_for(annotation: Any) -> Any:
    if annotation in (str, None):
        return ""
    if annotation is int:
        return 0
    if annotation is float:
        return 0.0
    if annotation is bool:
        return False
    origin = getattr(annotation, "__origin__", None)
    if origin in (list, set, tuple):
        return []
    if origin is dict:
        return {}
    return ""


class FakeStructuredRunnable:
    """Stands in for `make_llm(...).with_structured_output(schema)`."""

    def __init__(self, schema: type) -> None:
        self._schema = schema

    def _build(self, input_value: Any) -> Any:
        text = _text_of(input_value)
        kwargs = _canned_kwargs(self._schema.__name__, text)
        if kwargs is not None:
            return self._schema(**kwargs)
        return _generic_instance(self._schema)

    def invoke(self, input_value: Any, config: Any = None, **kwargs: Any) -> Any:
        return self._build(input_value)

    async def ainvoke(self, input_value: Any, config: Any = None, **kwargs: Any) -> Any:
        return self._build(input_value)
