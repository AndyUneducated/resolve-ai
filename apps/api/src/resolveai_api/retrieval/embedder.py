"""Embedding factory — 调库优先，直接复用 LangChain 现成 Embeddings 客户端。

与 [`core/llm.py`](../core/llm.py) 的 cost-aware routing 同构：
- backend="ollama" → `OllamaEmbeddings`（默认 `bge-m3`，1024 维，本地无外呼）
- backend="openai" → `OpenAIEmbeddings`

不自建推理服务；维度强校验放在 seed / query 调用方，确保与
`kb_documents.embedding vector(1024)` 一致。
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
