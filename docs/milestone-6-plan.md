# Milestone 6 — Hybrid Retrieval

**Status:** Implemented (see [roadmap.md](roadmap.md) Milestone 6).

**Goal:** Ship a hybrid KB retrieval stack (BM25 + dense + RRF + reranker) and make the Technical Agent produce KB-grounded, citation-verified answers — while abstracting the interfaces, tenant filtering, observability, and eval hooks that M7 (architecture ablation), M8 (chaos/demo), and M9 (multi-tenant) build on.

**Design principle:** 调库优先 (library-first). Reuse the database and existing frameworks; project code is only a thin orchestration/contract layer.

- BM25 → PostgreSQL `ts_rank_cd` (no custom inverted index)
- Dense → pgvector `<=>` cosine (no custom ANN engine)
- Embeddings → `langchain-ollama` / `langchain-openai` (no self-hosted inference)
- Reranker → `sentence-transformers` CrossEncoder (optional extra, graceful fallback)
- DB access → SQLAlchemy async + psycopg (already in deps)

---

## 1. What shipped

| Area | Implementation |
|---|---|
| Shared types | `retrieval/types.py` — `RetrievedDoc` (unified contract; `id` = KB doc id used for citation + grounding) and `RetrievalTrace` (observability snapshot) |
| Embedding | `retrieval/embedder.py` — factory mirroring `core/llm.py`; default `bge-m3` (1024-dim, aligns with `kb_documents.embedding vector(1024)`) |
| Store | `retrieval/store.py` — async `dense_search` (pgvector cosine) + `lexical_search` (Postgres `ts_rank_cd`), both **forced tenant-scoped** |
| Fusion | `retrieval/fusion.py` — pure Reciprocal Rank Fusion (`k=60` default), deterministic tie-break |
| Reranker | `retrieval/reranker.py` — lazy `bge-reranker-v2-m3` CrossEncoder; any failure (missing extra / no torch / inference error) degrades to RRF order |
| Orchestrator | `retrieval/hybrid.py` — `HybridRetriever` ties embedder→store→fusion→reranker; `hybrid` / `dense_only` profiles; emits OTel span + `RetrievalTrace` |
| Factory | `retrieval/__init__.py` — `get_retriever()` (lazy: no DB/embedder until first `search`) |
| Metrics | `retrieval/metrics.py` — Recall@k / proportional Recall@k / MRR@k pure functions |
| Agent grounding | `agents/technical.py` rewritten: KB retrieve → chunk scan → structured `TechnicalAnswer` with citations verified ⊆ retrieved doc ids (hallucinated ids dropped + flagged) |
| Post-retrieval guardrail | `guardrails/retrieval_filter.py` — scans retrieved chunks for indirect injection / KB poisoning (reuses L1 patterns), quarantines poisoned docs (closes M5's input-only gap) |
| Seed | `scripts/seed_db.py` — idempotent, dim-checked ingestion of 53 FAQ/runbook docs + embeddings |
| Retrieval eval | `scripts/eval_retrieval.py` — Recall/MRR per profile on a golden set (hybrid vs dense-only comparison harness for M7) |
| Datasets | `apps/api/tests/fixtures/kb_documents.jsonl` (53 docs), `apps/api/tests/fixtures/kb_retrieval_golden.jsonl` (22 query→expected-title cases) |
| Tests | `test_retrieval_fusion.py`, `test_retrieval_metrics.py`, `test_retrieval_chunk_scan.py`, rewritten `test_technical_agent.py`, guarded `test_retrieval_integration.py` |

---

## 2. Key file changes

- Retrieval package: `apps/api/src/resolveai_api/retrieval/{types,embedder,store,fusion,reranker,hybrid,metrics,__init__}.py`
- Technical Agent grounding: `apps/api/src/resolveai_api/agents/technical.py`
- Post-retrieval scan: `apps/api/src/resolveai_api/guardrails/retrieval_filter.py`
- Config surface: `apps/api/src/resolveai_api/config.py`
- Dependencies: `apps/api/pyproject.toml` (`sqlalchemy[asyncio]` for greenlet; optional `rerank` extra)
- Seed + eval scripts: `scripts/seed_db.py`, `scripts/eval_retrieval.py`
- Fixtures: `apps/api/tests/fixtures/kb_documents.jsonl`, `apps/api/tests/fixtures/kb_retrieval_golden.jsonl`
- Tests: `apps/api/tests/test_retrieval_fusion.py`, `test_retrieval_metrics.py`, `test_retrieval_chunk_scan.py`, `test_technical_agent.py`, `test_retrieval_integration.py`

---

## 3. Configuration knobs added

- Embedding: `EMBEDDING_BACKEND=ollama|openai`, `EMBEDDING_MODEL` (default `bge-m3`), `EMBEDDING_DIM` (default `1024`)
- Retrieval: `RETRIEVAL_PROFILE=hybrid|dense_only`, `RETRIEVAL_TOP_K` (5), `RETRIEVAL_CANDIDATE_K` (50), `RETRIEVAL_RRF_K` (60)
- Reranker: `RERANKER_ENABLED=on|off`, `RERANKER_MODEL` (default `BAAI/bge-reranker-v2-m3`)

`dense_only` is the roadmap's documented fall-back path and doubles as an M7 ablation variant.

---

## 4. How to run

Seed the KB (requires Postgres + an embedding model, e.g. `ollama pull bge-m3`):

```bash
uv run python scripts/seed_db.py --truncate
```

Evaluate retrieval quality (hybrid vs dense-only):

```bash
uv run python scripts/eval_retrieval.py --profiles hybrid,dense_only --k 5
```

Enable the reranker (optional, pulls torch):

```bash
uv sync --extra rerank   # in apps/api
```

---

## 5. Validation executed in this milestone

Executed:

- `uv run python -m pytest apps/api/tests --ignore=apps/api/tests/test_llm_live.py -q` → `85 passed`
- Live seed of 53 docs into `resolveai-postgres`, then `scripts/eval_retrieval.py`

Result:

- Retrieval eval (golden set, k=5): `recall@5=1.000`, `prop_recall@5=1.000`, `mrr@5=1.000` for both `hybrid` and `dense_only`
- Live integration test (lexical search + tenant isolation) passes against real Postgres
- Reranker correctly degraded to RRF order when `sentence-transformers` was not installed

Coverage highlights:

- RRF fusion math and ranking determinism
- Recall@k / MRR@k metric correctness
- Chunk scanner quarantines KB-poisoning / injection content
- Technical Agent grounding: citations verified ⊆ retrieved ids; hallucinated ids flagged; empty-corpus → escalation

---

## 6. Forward adaptation for M7 / M8 / M9

- **M7 (architecture ablation):** `RetrievalTrace` (doc ids, per-path ids, latency) and the profile switch feed the ablation runner; `eval_retrieval.py` provides the retrieval-quality dimension. Note: the current golden set is easy enough that hybrid and dense-only both score 1.0 — add keyword-heavy / harder queries to demonstrate hybrid's advantage.
- **M8 (chaos/demo):** retrieval emits an OTel span (`retrieval.search`) with profile / counts / result ids / latency for online regression and demo traces.
- **M9 (multi-tenant):** every query is `tenant_id`-scoped, ready to migrate to Postgres RLS. The same `HybridRetriever` abstraction is reusable by `Memory.search_long_term` with a stricter `tenant_id + customer_id` filter.

---

## 7. Notes

- The grounding contract ("cited doc ids ⊆ retrieved set") is a clean, deterministic signal — distinct from M5's `attribution_correct` (which targets guardrail layers, not KB citations).
- `sqlalchemy[asyncio]` was required because `create_async_engine` needs greenlet (the existing checkpointer used psycopg async directly, so it wasn't previously pulled in).
- The unused `rank-bm25` dependency is intentionally left in place; the BM25 path uses Postgres `ts_rank_cd`, so `rank-bm25` can be removed in a later cleanup if desired.
