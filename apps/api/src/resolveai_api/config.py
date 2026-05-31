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
    # The remaining 4 servers ship as TOOLS-only stubs in M2; their stdio impl
    # lands in M3. Setting any of these to a real cmd auto-enables them.
    mcp_zendesk_cmd: str = Field(default="", alias="MCP_ZENDESK_CMD")
    mcp_slack_cmd: str = Field(default="", alias="MCP_SLACK_CMD")
    mcp_salesforce_cmd: str = Field(default="", alias="MCP_SALESFORCE_CMD")
    mcp_intercom_cmd: str = Field(default="", alias="MCP_INTERCOM_CMD")
    sandbox_mode: str = Field(default="off", alias="SANDBOX_MODE")
    mcp_sandbox_image: str = Field(
        default="resolveai/mcp-servers:dev", alias="MCP_SANDBOX_IMAGE"
    )

    # ---------- Guardrails ----------
    llama_guard_endpoint: str = Field(default="", alias="LLAMA_GUARD_ENDPOINT")
    llama_guard_model: str = Field(default="llama-guard3:8b", alias="LLAMA_GUARD_MODEL")
    llama_guard_timeout_ms: int = Field(default=2000, alias="LLAMA_GUARD_TIMEOUT_MS")
    presidio_endpoint: str = Field(default="", alias="PRESIDIO_ENDPOINT")
    presidio_language: str = Field(default="en", alias="PRESIDIO_LANGUAGE")
    policy_judge_model: str = Field(default="qwen3.5:9b", alias="POLICY_JUDGE_MODEL")
    policy_judge_timeout_ms: int = Field(default=1500, alias="POLICY_JUDGE_TIMEOUT_MS")
    gvisor_runtime: str = Field(default="runsc", alias="GVISOR_RUNTIME")
    guardrail_l1: str = Field(default="on", alias="GUARDRAIL_L1")
    guardrail_l2: str = Field(default="on", alias="GUARDRAIL_L2")
    guardrail_l3: str = Field(default="on", alias="GUARDRAIL_L3")
    guardrail_l4: str = Field(default="on", alias="GUARDRAIL_L4")

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

    # ---------- Observability ----------
    otel_endpoint: str = Field(
        default="http://localhost:4318", alias="OTEL_EXPORTER_OTLP_ENDPOINT"
    )
    otel_service_name: str = Field(default="resolveai-api", alias="OTEL_SERVICE_NAME")
    evalgate_endpoint: str = Field(default="", alias="EVALGATE_ENDPOINT")

    # ---------- Tenant ----------
    default_tenant_id: str = Field(default="demo", alias="DEFAULT_TENANT_ID")

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.api_cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
