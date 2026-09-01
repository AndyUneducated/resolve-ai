"""Embedding factory that reuses established LangChain embedding clients.

This mirrors the cost-aware routing in [`core/llm.py`](../core/llm.py):
- backend="ollama" → `OllamaEmbeddings` (default `bge-m3`, 1,024 dimensions,
  with no external calls)
- backend="openai" → `OpenAIEmbeddings`

No custom inference service is introduced. Seed and query callers strictly
validate dimensions against `kb_documents.embedding vector(1024)`.
"""

from __future__ import annotations

from functools import lru_cache

from langchain_core.embeddings import Embeddings

from resolveai_api.config import get_settings


def make_embedder() -> Embeddings:
    """Return a LangChain Embeddings client bound to the configured backend."""
    settings = get_settings()
    backend = settings.embedding_backend

    if backend == "ollama":
        from langchain_ollama import OllamaEmbeddings

        return OllamaEmbeddings(
            model=settings.embedding_model,
            base_url=settings.ollama_base_url,
        )

    if backend == "openai":
        from langchain_openai import OpenAIEmbeddings

        return OpenAIEmbeddings(model=settings.embedding_model)

    raise ValueError(
        f"Unsupported EMBEDDING_BACKEND={backend!r}; expected 'ollama' or 'openai'."
    )


@lru_cache
def get_embedder() -> Embeddings:
    """Process-wide cached embedder (model handle is reused across requests)."""
    return make_embedder()
