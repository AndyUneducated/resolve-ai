-- ResolveAI · Migration 0001 — 多租户硬隔离 (Postgres Row-Level Security)
--
-- 把租户隔离从「应用层强制 WHERE tenant_id」下沉到「数据库层强制」：即使某条
-- SQL 忘了带 WHERE tenant_id，RLS 也物理拦住跨租户读 / 写 / 删 (defense-in-depth)。
--
-- 关键决策（见 docs/milestone-9-plan.md §7）：
--   * FORCE ROW LEVEL SECURITY —— 表 owner (resolveai) 默认 bypass RLS，FORCE 让
--     owner 也受 policy 约束，避免「应用用 owner 角色连库就绕过隔离」的经典坑。
--   * current_setting('app.tenant_id', true) —— 第二参 true = missing_ok：未 SET
--     时返回 NULL 而非报错；NULL 与任何 tenant_id 比较为 false → 0 行 (fail-closed)。
--   * 租户上下文由应用在事务内 SET LOCAL app.tenant_id 注入 (core/db.py)。
--
-- 幂等：DROP POLICY IF EXISTS + CREATE POLICY；ALTER ... ENABLE/FORCE 可重复执行。
-- 应用方式 (owner 角色)：psql "$DATABASE_URL" -f infra/docker/migrations/0001_rls.sql

BEGIN;

-- ---------------------------------------------------------------------------
-- 业务表：按 tenant_id 列隔离 (customers / tickets / kb_documents /
-- agent_checkpoints)。USING 控制可见 (SELECT/UPDATE/DELETE)；WITH CHECK 防写他租户行。
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
-- tenants 注册表：没有 tenant_id 列，按主键 id 隔离 —— 一个租户只能看见 / 改
-- 自己那条注册记录。
-- ---------------------------------------------------------------------------

ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenants FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_self ON tenants;
CREATE POLICY tenant_self ON tenants
    USING (id = current_setting('app.tenant_id', true))
    WITH CHECK (id = current_setting('app.tenant_id', true));

-- 注意：LangGraph checkpoint 表 (checkpoints / checkpoint_writes /
-- checkpoint_blobs) 由 AsyncPostgresSaver 自管、无 tenant_id 列，RLS 套不上；
-- 其隔离继续由 app 层 IsolatedCheckpointer (tenant::customer::thread 命名空间校验)
-- 负责。这是有意的边界，见 docs/milestone-9-plan.md §7。

-- ---------------------------------------------------------------------------
-- 低权限应用角色 resolveai_app（§7 决策 A，已修正）
--
-- 关键事实：SUPERUSER / BYPASSRLS 角色**无条件绕过 RLS**，FORCE 只对「非超级用户的
-- 表 owner」生效。demo 的 resolveai 是 POSTGRES_USER 超级用户，所以应用若继续用它连库，
-- RLS 形同虚设。故这里建一个 NOSUPERUSER NOBYPASSRLS 的 resolveai_app：
--   * 应用运行时（KbStore 等 tenant-scoped SQLAlchemy 查询）以 resolveai_app 连库
--     （APP_DATABASE_URL），RLS 真正生效；
--   * resolveai 仍用于迁移 / seed / 扩展 / LangGraph checkpoint setup（需要 owner/超级权限）。
-- ---------------------------------------------------------------------------

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'resolveai_app') THEN
        CREATE ROLE resolveai_app LOGIN PASSWORD 'resolveai_app'
            NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
    ELSE
        -- 幂等且防回归：确保它永远不是超级用户、不绕过 RLS。
        ALTER ROLE resolveai_app NOSUPERUSER NOBYPASSRLS;
    END IF;
END
$$;

GRANT USAGE ON SCHEMA public TO resolveai_app;
GRANT SELECT, INSERT, UPDATE, DELETE
    ON customers, tickets, kb_documents, agent_checkpoints, tenants
    TO resolveai_app;
-- kb_documents.id 是 BIGSERIAL，INSERT 需要序列权限。
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO resolveai_app;

COMMIT;
