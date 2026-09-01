"""LLM factory — cost-aware routing across local (Ollama) / cloud (Anthropic).

Decision 1 · Cost-aware routing:
- tier="triage" uses a small model (default `qwen3.5:9b`) for frequent,
  low-complexity work such as intent classification
- tier="vertical" uses a specialized model (default `qwen3.5:9b`, matching
  local validation; production may set `qwen3.6:27b` in .env)

Industry-aligned approach: `ChatOllama` is LangChain's recommended local LLM
client, and the Qwen3.5+ family natively supports OpenAI-style tool calling and
JSON mode.
"""

from __future__ import annotations

from typing import Literal

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.runnables import Runnable
from pydantic import BaseModel

from resolveai_api.config import get_settings

LLMTier = Literal["triage", "vertical"]


def _model_name(tier: LLMTier) -> str:
    s = get_settings()
    return s.triage_model if tier == "triage" else s.vertical_model


def make_llm(tier: LLMTier, *, temperature: float = 0.0) -> BaseChatModel:
    """Return a chat model bound to the configured backend + tier.

    Every model carries a tier-tagged usage callback; it is a no-op unless a
    `core.usage.capture_run()` trace is active (M7 ablation accounting), so the
    production path is unchanged.
    """
    from resolveai_api.core.usage import tier_callback

    settings = get_settings()
    model = _model_name(tier)
    # Typed as the base handler so the (invariant) `callbacks` param accepts it.
    callbacks: list[BaseCallbackHandler] = [tier_callback(tier)]

    if settings.llm_backend == "fake":
        from resolveai_api.core._fake_llm import FakeChatModel

        return FakeChatModel(callbacks=callbacks)

    if settings.llm_backend == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=model,
            base_url=settings.ollama_base_url,
            temperature=temperature,
            callbacks=callbacks,
        )

    if settings.llm_backend == "anthropic":
        from langchain_anthropic import ChatAnthropic

        # `model` is accepted at runtime (alias of model_name); the stub only
        # declares model_name, hence the targeted ignore.
        return ChatAnthropic(  # type: ignore[call-arg]
            model=model, temperature=temperature, callbacks=callbacks
        )

    raise ValueError(
        f"Unsupported LLM_BACKEND={settings.llm_backend!r}; expected 'ollama' | 'anthropic' | 'fake'."
    )


def make_structured_llm(
    tier: LLMTier,
    schema: type[BaseModel],
    *,
    temperature: float = 0.0,
) -> Runnable:
    """Return a runnable that emits a Pydantic instance of `schema`.

    Uses `with_structured_output` (LangChain 1.x standard) — local Ollama goes
    through JSON mode; cloud providers use native structured-output APIs.
    """
    if get_settings().llm_backend == "fake":
        from resolveai_api.core._fake_llm import FakeStructuredRunnable

        return FakeStructuredRunnable(schema)
    return make_llm(tier, temperature=temperature).with_structured_output(schema)
