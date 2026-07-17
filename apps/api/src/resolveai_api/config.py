"""集中配置 — 单一来源，pydantic-settings 从环境变量 / .env 读取。"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---------- LLM ----------
    llm_backend: str = Field(default="ollama", alias="LLM_BACKEND")
    """ollama (default) | anthropic | fake — switch via env without code changes.

    `fake` returns deterministic, zero-latency canned responses (M8 chaos load
    testing); it never touches the network.
    """
    ollama_base_url: str = Field(
        default="http://127.0.0.1:11434", alias="OLLAMA_BASE_URL"
    )

    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")

    # 决策 1 · Cost-aware Routing（本地验证默认双 9B；生产可设 VERTICAL_MODEL=qwen3.6:27b）
    triage_model: str = Field(default="qwen3.5:9b", alias="TRIAGE_MODEL")
    vertical_model: str = Field(default="qwen3.5:9b", alias="VERTICAL_MODEL")

    # ---------- DB ----------
    database_url: str = Field(
        default="postgresql+psycopg://resolveai:resolveai@localhost:5432/resolveai",
        alias="DATABASE_URL",
    )
    """管理员 DSN（resolveai，超级用户）：迁移 / seed / 扩展 / LangGraph checkpoint setup。"""

    app_database_url: str = Field(default="", alias="APP_DATABASE_URL")
    """应用运行时 DSN（M9）：tenant-scoped SQLAlchemy 查询（KbStore）以此连库。

    应指向低权限角色 `resolveai_app`（NOSUPERUSER/NOBYPASSRLS），RLS 才会真正生效——
    超级用户 / BYPASSRLS 角色无条件绕过 RLS，FORCE 也拦不住。留空则回退 `database_url`
    （仅适用于未应用 0001_rls.sql 迁移、或刻意不启用硬隔离的环境）。"""

    @property
    def app_dsn(self) -> str:
        """Effective DSN for the tenant-scoped app path (falls back to admin DSN)."""
        return self.app_database_url or self.database_url

    # ---------- Checkpointer ----------
    checkpoint_backend: str = Field(default="postgres", alias="CHECKPOINT_BACKEND")
    """postgres (default) | memory — memory used in tests, postgres in dev/prod."""

    @property
    def psycopg_dsn(self) -> str:
        """LangGraph AsyncPostgresSaver wants raw psycopg DSN, not SQLAlchemy URL."""
        return self.database_url.replace("postgresql+psycopg://", "postgresql://", 1)

    # ---------- API ----------
    api_host: str = Field(default="0.0.0.0", alias="API_HOST")
    api_port: int = Field(default=8000, alias="API_PORT")
    api_log_level: str = Field(default="info", alias="API_LOG_LEVEL")
    api_cors_origins: str = Field(default="http://localhost:3000", alias="API_CORS_ORIGINS")

    # ---------- MCP ----------
    mcp_transport: str = Field(default="stdio", alias="MCP_TRANSPORT")
    mcp_stripe_cmd: str = Field(default="python -m mcp_servers.stripe", alias="MCP_STRIPE_CMD")
    # All 5 servers are real stdio MCP servers (M3). Only Stripe is enabled by
    # default here to keep a minimal boot; `.env.example` enables all 5. Setting
    # any of these to a real cmd auto-enables that server (see `registry.py`).
    mcp_zendesk_cmd: str = Field(default="", alias="MCP_ZENDESK_CMD")
    mcp_slack_cmd: str = Field(default="", alias="MCP_SLACK_CMD")
    mcp_salesforce_cmd: str = Field(default="", alias="MCP_SALESFORCE_CMD")
    mcp_intercom_cmd: str = Field(default="", alias="MCP_INTERCOM_CMD")
    sandbox_mode: str = Field(default="off", alias="SANDBOX_MODE")
    mcp_sandbox_image: str = Field(
        default="resolveai/mcp-servers:dev", alias="MCP_SANDBOX_IMAGE"
    )
    # ---------- Sandbox policy (M10 · Layer 2 blast-radius containment) ----------
    # Per tool-call resource / isolation budget. Enforced by the container runtime
    # (gVisor `runsc` / `runc`) when available, else best-effort by the subprocess
    # backend (POSIX rlimits + wall timeout). Network / read-only-fs are only
    # enforceable by the container tier — the subprocess tier reports them as
    # `degraded`, which is exactly why blast-radius containment needs gVisor.
    sandbox_cpu_seconds: int = Field(default=5, alias="SANDBOX_CPU_SECONDS")
    sandbox_memory_mb: int = Field(default=256, alias="SANDBOX_MEMORY_MB")
    sandbox_wall_timeout_s: float = Field(default=10.0, alias="SANDBOX_WALL_TIMEOUT_S")
    sandbox_max_processes: int = Field(default=64, alias="SANDBOX_MAX_PROCESSES")
    sandbox_max_file_mb: int = Field(default=16, alias="SANDBOX_MAX_FILE_MB")
    sandbox_network: str = Field(default="none", alias="SANDBOX_NETWORK")  # none|allowlist
    # App profile: "demo" (fail-open, favor availability) | "production"
    # (fail-closed guardrails + sandbox enforced by default).
    env_profile: str = Field(default="demo", alias="ENV_PROFILE")

    # ---------- Guardrails ----------
    llama_guard_endpoint: str = Field(default="", alias="LLAMA_GUARD_ENDPOINT")
    llama_guard_model: str = Field(default="llama-guard3:8b", alias="LLAMA_GUARD_MODEL")
    llama_guard_timeout_ms: int = Field(default=2000, alias="LLAMA_GUARD_TIMEOUT_MS")
    presidio_endpoint: str = Field(default="", alias="PRESIDIO_ENDPOINT")
    presidio_language: str = Field(default="en", alias="PRESIDIO_LANGUAGE")
    presidio_ignored_entities: str = Field(
        default="DATE_TIME", alias="PRESIDIO_IGNORED_ENTITIES"
    )
    """Comma-separated Presidio entity types to NOT treat as PII (redaction scope only).

    `DATE_TIME` fires on benign phrases like "yesterday" / "last month", producing
    noisy `pii:date_time` flags that aren't blocking and aren't sensitive. Excluding
    them cuts noise without changing any blocking decision. Set to "" to redact all
    detected entity types."""
    policy_judge_model: str = Field(default="qwen3.5:9b", alias="POLICY_JUDGE_MODEL")
    # Local Ollama judges (esp. a 27B vertical model) routinely exceed 1.5s; that
    # default produced near-constant policy_judge_timeout flags during live eval.
    # Keep the budget generous for local; lower only against a fast hosted endpoint.
    policy_judge_timeout_ms: int = Field(default=8000, alias="POLICY_JUDGE_TIMEOUT_MS")

    @property
    def presidio_ignored_entities_set(self) -> set[str]:
        return {
            e.strip().upper()
            for e in self.presidio_ignored_entities.split(",")
            if e.strip()
        }
    gvisor_runtime: str = Field(default="runsc", alias="GVISOR_RUNTIME")
    guardrail_l1: str = Field(default="on", alias="GUARDRAIL_L1")
    guardrail_l2: str = Field(default="on", alias="GUARDRAIL_L2")
    guardrail_l3: str = Field(default="on", alias="GUARDRAIL_L3")
    guardrail_l4: str = Field(default="on", alias="GUARDRAIL_L4")
    # Fail-closed: a guard that times out / is unavailable (Llama Guard, Presidio,
    # policy judge) causes the request to be BLOCKED rather than passed through.
    #   "on"/"off" = explicit override; "auto" = follow `env_profile`
    #   (production → closed, demo → open). See `attribution.resolve_fail_closed`.
    guardrail_fail_closed: str = Field(default="auto", alias="GUARDRAIL_FAIL_CLOSED")

    # ---------- Retrieval (M6 · Hybrid Retrieval) ----------
    # 调库优先：embedding 走 langchain-ollama / langchain-openai 现成 Embeddings；
    # 向量检索走 pgvector；BM25 走 Postgres ts_rank_cd；精排走 sentence-transformers。
    embedding_backend: str = Field(default="ollama", alias="EMBEDDING_BACKEND")
    """ollama (default) | openai — embedding 客户端后端。"""
    embedding_model: str = Field(default="bge-m3", alias="EMBEDDING_MODEL")
    """默认 bge-m3 → 1024 维，与 kb_documents.embedding vector(1024) 对齐。"""
    embedding_dim: int = Field(default=1024, alias="EMBEDDING_DIM")
    """强校验维度，seed 与 query 两端必须一致。"""

    retrieval_profile: str = Field(default="hybrid", alias="RETRIEVAL_PROFILE")
    """hybrid (BM25 + dense + RRF) | dense_only (降级路径，用于 M7 ablation)。"""
    retrieval_top_k: int = Field(default=5, alias="RETRIEVAL_TOP_K")
    """最终返回给 Agent 的文档数（reranker 之后）。"""
    retrieval_candidate_k: int = Field(default=50, alias="RETRIEVAL_CANDIDATE_K")
    """每路召回的候选数（送入 RRF 融合）。"""
    retrieval_rrf_k: int = Field(default=60, alias="RETRIEVAL_RRF_K")
    """RRF 常数 k；越大越平滑，行业默认 60。"""

    reranker_enabled: str = Field(default="on", alias="RERANKER_ENABLED")
    """on | off — 关掉则回退 RRF 融合排序（reranker 依赖未装时自动降级）。"""
    reranker_model: str = Field(
        default="BAAI/bge-reranker-v2-m3", alias="RERANKER_MODEL"
    )

    # ---------- Semantic cache (M13) ----------
    semantic_cache_enabled: str = Field(default="off", alias="SEMANTIC_CACHE_ENABLED")
    """on | off（默认）— on 时对 KB 检索做 embedding 近邻语义缓存（降 DB 往返 +
    rerank 计算）。默认 off，检索路径与 M13 前逐字节一致。"""
    semantic_cache_threshold: float = Field(default=0.95, alias="SEMANTIC_CACHE_THRESHOLD")
    """余弦相似度阈值；≥ 该值判定语义命中（越高越保守，越不易串答案）。"""
    semantic_cache_ttl_s: float = Field(default=3600.0, alias="SEMANTIC_CACHE_TTL_S")
    """缓存条目 TTL（秒），过期即失效防陈旧。<=0 表示不过期。"""
    semantic_cache_max_entries: int = Field(default=512, alias="SEMANTIC_CACHE_MAX_ENTRIES")
    """每进程缓存容量上限；超出按最旧淘汰（LRU-ish）。"""

    # ---------- Observability ----------
    otel_endpoint: str = Field(
        default="http://localhost:4318", alias="OTEL_EXPORTER_OTLP_ENDPOINT"
    )
    otel_service_name: str = Field(default="resolveai-api", alias="OTEL_SERVICE_NAME")
    evalgate_endpoint: str = Field(default="", alias="EVALGATE_ENDPOINT")
    # Eval→data flywheel (M14): when set, each terminal ticket appends a
    # PII-scrubbed JSON line here (best-effort, never fails a request). Ops feeds
    # this file to `scripts/harvest_traces.py`. Empty (default) = disabled.
    trace_sink_path: str = Field(default="", alias="TRACE_SINK_PATH")
    # Per-ticket modeled-cost budget (USD). When a run's accrued cost exceeds this,
    # the vertical Plan-Execute / ReAct loop stops spending (protective degrade) and
    # the Supervisor flags `cost:budget_exceeded`. `<= 0` disables the breaker.
    # Default is deliberately generous for the demo; tighten in production profiles.
    cost_budget_usd: float = Field(default=0.05, alias="COST_BUDGET_USD")

    # ---------- Human-in-the-Loop (M12) ----------
    approval_mode: str = Field(default="off", alias="APPROVAL_MODE")
    """off (default — no gate, byte-identical to pre-M12) | destructive (park every
    destructive-capability tool call for human approve/deny/edit) | auto (follow
    ENV_PROFILE — `production` → destructive, else off). The gate lives at the
    Executor chokepoint; parked runs emit an `awaiting_approval` SSE event."""

    # ---------- Tenant ----------
    default_tenant_id: str = Field(default="demo", alias="DEFAULT_TENANT_ID")

    # ---------- Multi-tenant isolation (M9 · Postgres RLS) ----------
    rls_enabled: str = Field(default="on", alias="RLS_ENABLED")
    """on (default) | off — on 时 tenant-scoped 查询走 `tenant_session`（事务内
    `SET LOCAL app.tenant_id`）以激活数据库 RLS policy；off 时退回纯 app 层
    `WHERE tenant_id` 过滤（用于未应用 0001_rls.sql 迁移的环境）。"""

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.api_cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
