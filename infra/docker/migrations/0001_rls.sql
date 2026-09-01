-- ResolveAI · Migration 0001 — strict multi-tenant isolation (Postgres Row-Level Security)
--
-- Move tenant isolation from application-enforced WHERE tenant_id clauses into the
-- database. Even if a query omits WHERE tenant_id, RLS blocks cross-tenant reads,
-- writes, and deletes (defense in depth).
--
-- Key decisions (see docs/milestone-9-plan.md §7):
--   * FORCE ROW LEVEL SECURITY — table owners (resolveai) bypass RLS by default.
--     FORCE subjects the owner to policies, preventing owner connections from
--     bypassing isolation.
--   * current_setting('app.tenant_id', true) — true means missing_ok: if the value
--     is not SET, it returns NULL instead of raising an error. NULL compared with
--     any tenant_id is false, yielding zero rows (fail closed).
--   * The application injects tenant context with SET LOCAL app.tenant_id inside
--     each transaction (core/db.py).
--
-- Idempotent: DROP POLICY IF EXISTS + CREATE POLICY; ALTER ... ENABLE/FORCE can
-- be run repeatedly.
-- Apply as the owner role:
-- psql "$DATABASE_URL" -f infra/docker/migrations/0001_rls.sql

BEGIN;

-- ---------------------------------------------------------------------------
-- Business tables are isolated by tenant_id (customers / tickets / kb_documents /
-- agent_checkpoints). USING controls visibility (SELECT/UPDATE/DELETE); WITH CHECK
-- prevents writes into another tenant.
-- ---------------------------------------------------------------------------

ALTER TABLE customers ENABLE ROW LEVEL SECURITY;
ALTER TABLE customers FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON customers;
CREATE POLICY tenant_isolation ON customers
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

ALTER TABLE tickets ENABLE ROW LEVEL SECURITY;
ALTER TABLE tickets FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON tickets;
CREATE POLICY tenant_isolation ON tickets
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

ALTER TABLE kb_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE kb_documents FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON kb_documents;
CREATE POLICY tenant_isolation ON kb_documents
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

ALTER TABLE agent_checkpoints ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_checkpoints FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON agent_checkpoints;
CREATE POLICY tenant_isolation ON agent_checkpoints
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

-- ---------------------------------------------------------------------------
-- The tenants registry has no tenant_id column, so isolate it by primary key id:
-- each tenant can see and modify only its own registry row.
-- ---------------------------------------------------------------------------

ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenants FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_self ON tenants;
CREATE POLICY tenant_self ON tenants
    USING (id = current_setting('app.tenant_id', true))
    WITH CHECK (id = current_setting('app.tenant_id', true));

-- Note: LangGraph checkpoint tables (checkpoints / checkpoint_writes /
-- checkpoint_blobs) are managed by AsyncPostgresSaver and have no tenant_id
-- column, so RLS cannot be applied. Isolation remains the responsibility of the
-- application-level IsolatedCheckpointer (tenant::customer::thread namespace
-- validation). This is an intentional boundary; see docs/milestone-9-plan.md §7.

-- ---------------------------------------------------------------------------
-- Low-privilege application role resolveai_app (§7, Decision A, corrected)
--
-- Critical fact: SUPERUSER / BYPASSRLS roles always bypass RLS. FORCE applies
-- only to non-superuser table owners. The demo's resolveai POSTGRES_USER is a
-- superuser, so using it for application connections would render RLS ineffective.
-- Create a NOSUPERUSER NOBYPASSRLS resolveai_app role:
--   * At runtime, tenant-scoped SQLAlchemy queries (KbStore, etc.) connect as
--     resolveai_app via APP_DATABASE_URL, ensuring RLS is enforced.
--   * resolveai remains responsible for migrations, seeding, extensions, and
--     LangGraph checkpoint setup, which require owner/superuser privileges.
-- ---------------------------------------------------------------------------

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'resolveai_app') THEN
        CREATE ROLE resolveai_app LOGIN PASSWORD 'resolveai_app'
            NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
    ELSE
        -- Idempotent and regression-safe: ensure it is never a superuser and cannot bypass RLS.
        ALTER ROLE resolveai_app NOSUPERUSER NOBYPASSRLS;
    END IF;
END
$$;

GRANT USAGE ON SCHEMA public TO resolveai_app;
GRANT SELECT, INSERT, UPDATE, DELETE
    ON customers, tickets, kb_documents, agent_checkpoints, tenants
    TO resolveai_app;
-- kb_documents.id is BIGSERIAL, so INSERT requires sequence privileges.
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO resolveai_app;

COMMIT;
