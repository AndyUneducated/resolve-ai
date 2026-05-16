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
    """ollama (default) | anthropic — switch via env without code changes."""
    ollama_base_url: str = Field(
        default="http://127.0.0.1:11434", alias="OLLAMA_BASE_URL"
    )

    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")

    # 决策 1 · Cost-aware Routing（本地验证默认双 7B；生产可设 VERTICAL_MODEL=qwen2.5:32b）
    triage_model: str = Field(default="qwen2.5:7b", alias="TRIAGE_MODEL")
    vertical_model: str = Field(default="qwen2.5:7b", alias="VERTICAL_MODEL")

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

    # ---------- Guardrails ----------
    llama_guard_endpoint: str = Field(default="", alias="LLAMA_GUARD_ENDPOINT")
    presidio_endpoint: str = Field(default="", alias="PRESIDIO_ENDPOINT")
    gvisor_runtime: str = Field(default="runsc", alias="GVISOR_RUNTIME")

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
