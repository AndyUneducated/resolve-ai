# ResolveAI API image (M15 · one-click full stack).
#
# Build context MUST be the repo root (this is a uv *workspace*; the lockfile and
# all member manifests live above apps/api). See docker-compose.full.yml.
#
#   docker build -f apps/api/Dockerfile -t resolveai-api .
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

# curl is only needed for the container HEALTHCHECK (/healthz probe).
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# --- Dependency layer -------------------------------------------------------
# Copy ONLY the lockfile + every workspace member's manifest so this (slow)
# resolve/install layer stays cached until dependencies actually change.
COPY pyproject.toml uv.lock README.md ./
COPY apps/api/pyproject.toml apps/api/pyproject.toml
COPY packages/mcp-servers/zendesk/pyproject.toml packages/mcp-servers/zendesk/pyproject.toml
COPY packages/mcp-servers/stripe/pyproject.toml packages/mcp-servers/stripe/pyproject.toml
COPY packages/mcp-servers/slack/pyproject.toml packages/mcp-servers/slack/pyproject.toml
COPY packages/mcp-servers/salesforce/pyproject.toml packages/mcp-servers/salesforce/pyproject.toml
COPY packages/mcp-servers/intercom/pyproject.toml packages/mcp-servers/intercom/pyproject.toml
RUN uv sync --frozen --no-install-workspace --no-dev

# --- Source layer -----------------------------------------------------------
# Now bring in the actual code and install the workspace members (API + the 5
# MCP servers, so `python -m mcp_servers.<name>` stdio spawns work at runtime).
COPY apps ./apps
COPY packages ./packages
COPY scripts ./scripts
RUN uv sync --frozen --no-dev

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=40s --retries=6 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

# `python -m uvicorn` keeps the project interpreter (see Makefile note).
CMD ["uv", "run", "--no-sync", "python", "-m", "uvicorn", \
     "resolveai_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
