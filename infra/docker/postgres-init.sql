-- ResolveAI · Postgres initialization script
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Root table for multi-tenant isolation (Decision 4 · Layer 4 memory boundary)
CREATE TABLE IF NOT EXISTS tenants (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS customers (
    tenant_id    TEXT NOT NULL REFERENCES tenants(id),
    id           TEXT NOT NULL,
    email        TEXT,
    sla_tier     TEXT DEFAULT 'standard',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, id)
);

CREATE TABLE IF NOT EXISTS tickets (
    tenant_id      TEXT NOT NULL,
    id             TEXT NOT NULL,
    customer_id    TEXT NOT NULL,
    subject        TEXT,
    intent         TEXT,
    status         TEXT DEFAULT 'open',
    handoff_summary JSONB,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, id)
);

-- FAQ / runbook / KB retrieval (hybrid: BM25 via tsvector + dense via vector)
CREATE TABLE IF NOT EXISTS kb_documents (
    id             BIGSERIAL PRIMARY KEY,
    tenant_id      TEXT NOT NULL,
    title          TEXT NOT NULL,
    content        TEXT NOT NULL,
    content_tsv    tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    embedding      vector(1024),
    metadata       JSONB DEFAULT '{}'::jsonb,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS kb_tsv_idx       ON kb_documents USING GIN (content_tsv);
CREATE INDEX IF NOT EXISTS kb_embedding_idx ON kb_documents USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS kb_tenant_idx    ON kb_documents (tenant_id);

-- LangGraph state checkpoints (stateful handoff + interruption recovery)
CREATE TABLE IF NOT EXISTS agent_checkpoints (
    tenant_id      TEXT NOT NULL,
    customer_id    TEXT NOT NULL,
    thread_id      TEXT NOT NULL,
    checkpoint     JSONB NOT NULL,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, customer_id, thread_id)
);

INSERT INTO tenants (id, name) VALUES ('demo', 'Demo Tenant')
ON CONFLICT DO NOTHING;
