# Milestone 9 — Hard multi-tenant isolation (Postgres RLS)

**Status:** Implemented. Migration `infra/docker/migrations/0001_rls.sql`, `core/db.py` `tenant_session`, the `get_tenant_id` dependency, `KbStore` wiring, `seed_db.py` context injection, and `test_rls_isolation.py` (live PG negative tests, 3 passing) are all in place. **Decision A has been corrected** (see §7).

**Goal:** Push tenant isolation down from “application-enforced `tenant_id`” to “database-enforced.” This is the last layer of defense-in-depth: **even if a SQL statement forgets `WHERE tenant_id = :t`, Postgres Row-Level Security (RLS) physically blocks cross-tenant read / write / delete**. It is redundant with the existing app layer (`KbStore` always filters by tenant; `IsolatedCheckpointer` validates namespaces).

**Design principle:** Library-first — use native Postgres RLS (`CREATE POLICY` + `current_setting`), do not invent a custom row filter. DB access still goes through existing SQLAlchemy async + psycopg / LangGraph `AsyncPostgresSaver`; tenant context is injected only at the session boundary.

**Explicit non-goals:** Authentication (OAuth / login / session / API key / JWT), an RBAC role model, and an `/admin` config UI — this project **does not and will not** implement authn/authz. This milestone only lays the database foundation for data isolation. Tenant identity source is in §4.

> **Positioning (important):** No auth means RLS’s value is **defense-in-depth against application bugs** (a SQL statement that omitted `WHERE tenant_id`), **not against a malicious client** (without auth, the client still self-reports tenant). That is an honest, defensible senior narrative — do not claim it “stops a malicious tenant.”

Each request injects `tenant_id` into the transaction-scoped variable `app.tenant_id`; Postgres RLS policies filter rows accordingly. Critical prerequisite: connect as the **low-privilege role** `resolveai_app`, or a superuser will bypass RLS (see §7 Decision A).

```mermaid
flowchart TD
  req["Request"] --> dep["get_tenant_id dependency<br/>(demo fallback DEFAULT_TENANT_ID)"]
  dep --> sess["tenant_session(engine, tenant_id)"]
  sess --> setc["SET LOCAL inside the txn<br/>set_config('app.tenant_id', :t, true)"]
  setc --> q["KbStore query (no explicit WHERE tenant_id)"]
  q --> rls{"Postgres RLS policy"}
  rls -->|"read: USING (tenant_id = app.tenant_id)"| rows["Return only this tenant's rows"]
  rls -->|"write: WITH CHECK mismatch"| reject["Reject writes to another tenant's rows"]
  rls -->|"context unset → NULL → 0 rows"| closed["Default deny fail-closed"]
  role["⚠ Must connect as resolveai_app<br/>(NOSUPERUSER NOBYPASSRLS)"] -.-> rls
```

---

## 1. Current state (already in place)

- **Business tables already have `tenant_id`** (`infra/docker/postgres-init.sql`):
  `customers` / `tickets` / `kb_documents` / `agent_checkpoints` all use `tenant_id` as the tenant key; `tenants` is the tenant registry, primary key `id` (**no `tenant_id` column**; RLS needs a separate policy — see §3 / §5). The goal of a tenant_id column through the tables is largely met; this milestone only adds the audit layer.
- **App-layer isolation already runs for real**:
  - `KbStore` in `retrieval/store.py` forces `WHERE tenant_id = :tenant_id` on every dense/lexical query; there is no tenant-less full-corpus search.
  - `IsolatedCheckpointer` in `core/checkpointer.py` plus `guardrails/memory_isolator.py` `assert_match` checkpoint namespaces (`tenant::customer::thread`) and raise `CrossTenantAccessBlockedError` on cross-tenant access.
- **Two different DB access paths** (RLS is wired differently — see §3):
  1. **SQLAlchemy async engine** (`retrieval/store.py:get_engine()`, process-level `lru_cache`) — KB retrieval and future `tickets` queries.
  2. **LangGraph `AsyncPostgresSaver`** (`core/checkpointer.py`, self-managed psycopg pool and `checkpoints` / `checkpoint_writes` / `checkpoint_blobs` tables, organized by `thread_id`, **no tenant_id column**).

---

## 2. Key gaps

1. **RLS is not enabled**: any code that can connect can read/write across tenants; isolation depends entirely on application code not being wrong (exactly the “application bug” risk RLS is meant to catch).
2. **Table owner bypasses RLS**: docker init creates tables as role `resolveai`; **table owners bypass RLS by default**. This project uses `FORCE ROW LEVEL SECURITY` so the owner is also constrained (the classic pitfall of §7 Decision A).
3. **LangGraph checkpoint tables have no tenant column**: RLS cannot be applied to its internal tables; isolation remains the app-layer `IsolatedCheckpointer`. This milestone only needs to document that boundary.

> Tenant identity source is not a “gap” — there is no auth; `tenant_id` comes from request context (demo: `DEFAULT_TENANT_ID`). See §4.

---

## 3. Deliverables

| Area | Implementation |
|---|---|
| Migration | `infra/docker/migrations/0001_rls.sql` — `ENABLE` + `FORCE ROW LEVEL SECURITY` + `tenant_isolation` policy on 4 business tables (`customers` / `tickets` / `kb_documents` / `agent_checkpoints`); separate `id`-based policy on the `tenants` registry (see next row) |
| RLS policy | Business tables: `USING (tenant_id = current_setting('app.tenant_id', true))` (`true` = missing_ok; unset → NULL → 0 rows, fail-closed) + `WITH CHECK (...)` to block writes to another tenant’s rows. `tenants` table: `USING (id = current_setting('app.tenant_id', true))` (only the tenant’s own registry row) |
| Role | **Low-privilege `resolveai_app` (NOSUPERUSER/NOBYPASSRLS)** — the app connects as this role at runtime (`APP_DATABASE_URL`) so RLS actually takes effect; `resolveai` (superuser) is reserved for migrations / seed / extensions / LangGraph setup. **Correction of original Decision A**: FORCE only constrains a **non-superuser owner**; superuser / BYPASSRLS bypass RLS unconditionally (see §7) |
| Session injection (SQLAlchemy) | New `core/db.py` `tenant_session(engine, tenant_id)` async context manager: open txn → `SELECT set_config('app.tenant_id', :t, true)` (= SET LOCAL, but with bind params / injection safety) → yield conn. All tenant-scoped `KbStore` queries go through it (when `RLS_ENABLED=off`, fall back to app-layer filtering only) |
| Session injection (LangGraph) | `AsyncPostgresSaver` manages its own connections; per-call SET LOCAL is not possible. Checkpoint isolation stays with `IsolatedCheckpointer` (app layer). Document this boundary; optional future: set `options=-c app.tenant_id=...` on the saver’s connections |
| Tenant source | `api/dependencies.py` adds `get_tenant_id` — reads tenant_id from request context; demo falls back to `DEFAULT_TENANT_ID`. No auth, so no identity verification (see §4) |
| Chat wiring | `api/chat.py` uses `Depends(get_tenant_id)` to inject tenant context and feed `SET LOCAL` |
| Config | `RLS_ENABLED` (on/off, whether KbStore uses `tenant_session`); `APP_DATABASE_URL` (low-privilege DSN for app runtime; empty falls back to `DATABASE_URL`) |
| Tests | `test_rls_isolation.py` (live PG, guarded): tenant A cannot see tenant B rows; writes to another tenant are rejected by `WITH CHECK`; unset context → 0 rows |

---

## 4. Tenant identity source (no authentication)

This project does not and will not implement auth, so **no identity verification is introduced here**:

- Approach: `get_tenant_id` is a thin dependency that reads tenant_id from request context, falls back to `DEFAULT_TENANT_ID` in demo, and feeds the tenant to downstream `SET LOCAL`.
- That makes RLS’s role explicit: **defense-in-depth against application bugs** (the database still filters if `WHERE tenant_id` is omitted). It does not pretend to stop a malicious client — an honest boundary.
- The existing `tenant_id` path (`ChatRequest.tenant_id`, `stream(tenant_id=...)`, `DEFAULT_TENANT_ID`, `IsolatedCheckpointer` `user_tenant_id`) is unchanged; already-shipped M4 cross-tenant isolation is unaffected.

---

## 5. RLS policy draft

```sql
-- 4 business tables (kb_documents as example): isolate by tenant_id
ALTER TABLE kb_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE kb_documents FORCE ROW LEVEL SECURITY;  -- owner is constrained too

CREATE POLICY tenant_isolation ON kb_documents
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

-- tenants registry: no tenant_id column; isolate by primary key id (own row only)
ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenants FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_self ON tenants
    USING (id = current_setting('app.tenant_id', true));
```

```python
# core/db.py — inject tenant context per-request transaction (fail-closed)
@asynccontextmanager
async def tenant_session(engine: AsyncEngine, tenant_id: str):
    async with engine.begin() as conn:
        # set_config(..., is_local=true) == SET LOCAL, but supports bind params (SET does not),
        # injection-safe; valid only for this transaction; cleared when the connection returns to the pool.
        await conn.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_id},
        )
        yield conn
```

> The second argument to `current_setting('app.tenant_id', true)` `true` = return NULL instead of error if unset; NULL compared with any `tenant_id` is false → **default deny (fail-closed)**, which matches the security semantics.

---

## 6. How to run / verify

Apply the migration (owner role):

```bash
psql "$DATABASE_URL" -f infra/docker/migrations/0001_rls.sql
```

Run RLS isolation tests (need live Postgres + a low-privilege role connection, otherwise auto-skip):

```bash
APP_DATABASE_URL="postgresql+psycopg://resolveai_app:resolveai_app@localhost:5432/resolveai" \
  uv run python -m pytest apps/api/tests/test_rls_isolation.py -q
```

> Without `APP_DATABASE_URL` (i.e. connecting as superuser `resolveai`) these tests **skip** and say so — because a superuser bypasses RLS, so isolation cannot be measured. That skip is intentional and honest.

Manual smoke (**must connect as `resolveai_app`**, or a superuser bypasses RLS and you will see no effect):

```sql
SET app.tenant_id = 'demo';
SELECT count(*) FROM kb_documents;          -- demo rows only
SET app.tenant_id = 'acme';
SELECT count(*) FROM kb_documents;          -- acme rows only (demo not visible)
INSERT INTO kb_documents(tenant_id, title, content) VALUES ('demo', 'x', 'y');  -- WITH CHECK rejects
```

---

## 7. Risks / key decisions

- **Decision A — owner/superuser bypass (corrected during implementation):** The original plan bet that “`FORCE ROW LEVEL SECURITY` would constrain the `resolveai` owner as well,” **but live verification showed demo `resolveai` is the `POSTGRES_USER` superuser (`rolsuper=true`, `rolbypassrls=true`). Superuser / BYPASSRLS roles bypass RLS unconditionally — `FORCE` only applies to a non-superuser table owner.** Result: all 3 negative tests failed (RLS was effectively a no-op). Fix: the migration creates low-privilege role `resolveai_app` (`NOSUPERUSER NOBYPASSRLS`); the app connects as that role at runtime (`APP_DATABASE_URL`, only the tenant-scoped SQLAlchemy KB-retrieval path) so RLS actually takes effect; `resolveai` is reserved for migrations / seed / extensions / LangGraph setup. This is exactly the “production shape” the original text labeled “out of scope for this project” — it turned out not to be optional, but a prerequisite for RLS to work. Honest narrative: RLS isolation only holds when connecting as a **non-superuser**.
- **SET LOCAL must be inside a transaction:** wrap with `engine.begin()`. The process-level `lru_cache` pool is fine because `SET LOCAL` expires with the transaction and will not leak to the next request. Never use non-LOCAL `SET`, which would pollute pooled connections.
- **LangGraph checkpoints do not go through RLS:** those tables have no tenant column; isolation is the `IsolatedCheckpointer` fallback. That is an intentional boundary — document it in the blog / docs rather than pretending coverage.
- **pgvector HNSW + RLS:** RLS adds a filter predicate on the plan; the HNSW index still works, but recall counts may be affected by the row filter. M6 golden set is already 1.0; re-run `scripts/eval_retrieval.py` after the migration to confirm no regression.
- **seed_db.py:** under `FORCE RLS` the owner is also subject to the policy, so the seed transaction must `SET app.tenant_id = :tenant` (using the script’s existing `--tenant` value), or `WITH CHECK` on `tenants` / `kb_documents` will reject inserts.

---

## 8. Acceptance

- [x] 4 business tables `ENABLE` + `FORCE` RLS + `tenant_isolation` policy; `tenants` registry has a separate `tenant_self` (by `id`) policy.
- [x] Low-privilege `resolveai_app` role (`NOSUPERUSER NOBYPASSRLS`) + table/sequence grants, created idempotently in the migration (Decision A correction).
- [x] Live PG negative tests: tenant A cannot see / mutate (`WITH CHECK`) tenant B rows; unset context → 0 rows (fail-closed). 3 tests pass when connected as `resolveai_app`; honest skip when connected as superuser.
- [x] `get_tenant_id` in place: tenant injected from request context (no auth; demo fallback `DEFAULT_TENANT_ID`); `chat.py` uses `Depends(get_tenant_id)`.
- [x] Existing hermetic test suite all green (114 passed); retrieval integration tests pass under both roles.
- [x] Documented LangGraph checkpoint app-layer isolation boundary (no tenant column, not covered by RLS, `IsolatedCheckpointer` is the fallback).
- [x] Post-migration `scripts/eval_retrieval.py` recall regression check: `hybrid` / `dense_only` both `recall@5=1.000` (live verification 2026-05-31, report `reports/retrieval_eval_20260531_093508.json`).
