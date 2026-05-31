# Milestone 9 — 多租户硬隔离（Postgres RLS）

**Status:** 已实施。迁移 `infra/docker/migrations/0001_rls.sql`、`core/db.py` 的 `tenant_session`、`get_tenant_id` dependency、`KbStore` 接线、`seed_db.py` 上下文注入、`test_rls_isolation.py`（live PG 负向测试，3 项通过）均已落地。**Decision A 已修正**（见 §7）。

**Goal:** 把租户隔离从「应用层强制 `tenant_id`」下沉到「数据库层强制」。目标是 defense-in-depth 的最后一层：**即使某条 SQL 忘了带 `WHERE tenant_id = :t`，Postgres Row-Level Security (RLS) 也物理拦住跨租户读 / 写 / 删**。与现有 app 层（`KbStore` 强制 tenant 过滤、`IsolatedCheckpointer` 命名空间校验）互为冗余。

**Design principle:** 调库优先 —— 用 Postgres 原生 RLS（`CREATE POLICY` + `current_setting`），不自研 row filter；DB 访问继续走现有 SQLAlchemy async + psycopg / LangGraph `AsyncPostgresSaver`，仅在 session 边界注入租户上下文。

**明确不做（Non-goals）：** 鉴权（OAuth / 登录 / session / api-key / JWT）、RBAC 角色模型、`/admin` 配置 UI —— 本项目**不做也不计划做**任何认证/授权。本里程碑只做数据隔离的数据库地基。租户身份来源见 §4。

> **定位（重要）：** 不做鉴权，意味着 RLS 的价值是 **defense-in-depth 防"应用代码 bug"**（某条 SQL 漏了 `WHERE tenant_id`），**而非防"恶意客户端"**（无 auth 时客户端仍自报 tenant）。这是诚实且站得住的 senior 叙事——别把它说成"拦住恶意租户"。

---

## 1. 现状（已就绪的部分）

- **业务表已带 `tenant_id`**（`infra/docker/postgres-init.sql`）：
  `customers` / `tickets` / `kb_documents` / `agent_checkpoints` 4 张表均以 `tenant_id` 为租户键；`tenants` 是租户注册表，主键是 `id`（**无 `tenant_id` 列**，RLS 需单独处理，见 §3/§5）。tenant_id 列贯穿表的目标基本达成，本里程碑只补审计。
- **应用层隔离已真跑**：
  - `retrieval/store.py` 的 `KbStore` 每条 dense/lexical 查询强制 `WHERE tenant_id = :tenant_id`，无无租户全库检索。
  - `core/checkpointer.py` 的 `IsolatedCheckpointer` + `guardrails/memory_isolator.py` 对 checkpoint 命名空间（`tenant::customer::thread`）做 `assert_match`，跨租户访问抛 `CrossTenantAccessBlocked`。
- **两条不同的 DB 访问路径**（RLS 接入方式不同，见 §3）：
  1. **SQLAlchemy async engine**（`retrieval/store.py:get_engine()`，进程级 `lru_cache`）—— KB 检索、未来 `tickets` 查询。
  2. **LangGraph `AsyncPostgresSaver`**（`core/checkpointer.py`，自管 psycopg 连接池与 `checkpoints` / `checkpoint_writes` / `checkpoint_blobs` 表，按 `thread_id` 组织，**无 tenant_id 列**）。

---

## 2. 关键缺口

1. **RLS 未启用**：任何能连库的代码都能跨租户读写，隔离全靠应用代码不写错（正是 RLS 要兜底的"应用 bug"风险）。
2. **表 owner 绕过 RLS**：docker init 以 `resolveai` 角色建表，**表 owner 默认 bypass RLS**。本项目用 `FORCE ROW LEVEL SECURITY` 让 owner 也受约束（§7 决策 A 的经典坑）。
3. **LangGraph checkpoint 表无 tenant 列**：RLS 套不上其内部表；隔离继续由 app 层 `IsolatedCheckpointer` 负责，本里程碑文档化这一边界即可。

> 租户身份来源不在"缺口"之列——不做鉴权，tenant_id 直接来自请求上下文（demo 即 `DEFAULT_TENANT_ID`），见 §4。

---

## 3. 交付内容

| 领域 | Implementation |
|---|---|
| 迁移脚本 | `infra/docker/migrations/0001_rls.sql` —— 对 4 张业务表（`customers` / `tickets` / `kb_documents` / `agent_checkpoints`）`ENABLE` + `FORCE ROW LEVEL SECURITY` + `tenant_isolation` policy；`tenants` 注册表单独建基于 `id` 的 policy（见下行） |
| RLS policy | 业务表：`USING (tenant_id = current_setting('app.tenant_id', true))`（`true` = missing_ok，未 set 时返回 NULL → 0 行，fail-closed）+ `WITH CHECK (...)` 防写他租户行。`tenants` 表：`USING (id = current_setting('app.tenant_id', true))`（只见自己那条注册记录） |
| 角色 | **低权限 `resolveai_app`（NOSUPERUSER/NOBYPASSRLS）** —— 应用运行时以它连库（`APP_DATABASE_URL`），RLS 才真正生效；`resolveai`（超级用户）留作迁移/seed/扩展/LangGraph setup。**修正自原 Decision A**：FORCE 只约束「非超级用户 owner」，超级用户 / BYPASSRLS 无条件绕过 RLS（见 §7） |
| Session 注入（SQLAlchemy） | 新 `core/db.py` 的 `tenant_session(engine, tenant_id)` async context manager：开 txn → `SELECT set_config('app.tenant_id', :t, true)`（= SET LOCAL，但支持绑定参数、注入安全）→ yield conn。`KbStore` 所有 tenant-scoped 查询走它（`RLS_ENABLED=off` 时回退纯 app 层过滤） |
| Session 注入（LangGraph） | `AsyncPostgresSaver` 自管连接，无法 per-call SET LOCAL；checkpoint 隔离继续由 `IsolatedCheckpointer`（app 层）负责。文档化此边界；可选：给 saver 的连接加 `options=-c app.tenant_id=...` 留作 future |
| 租户来源 | `api/dependencies.py` 加 `get_tenant_id` dependency —— 从请求上下文取 tenant_id，demo 下回退 `DEFAULT_TENANT_ID`。不做鉴权，故不引入身份校验逻辑（见 §4） |
| Chat 接线 | `api/chat.py` 改用 `Depends(get_tenant_id)` 注入租户上下文，统一喂给 `SET LOCAL` |
| Config | `RLS_ENABLED`（on/off，gate KbStore 是否走 `tenant_session`）；`APP_DATABASE_URL`（应用运行时低权限 DSN，留空回退 `DATABASE_URL`） |
| Tests | `test_rls_isolation.py`（live PG，guarded）：set tenant A 看不到 tenant B 行；写入他租户被 `WITH CHECK` 拒；未 set context → 0 行 |

---

## 4. 租户身份来源（不做鉴权）

本项目不做也不计划做鉴权，所以这里**不引入任何身份校验**：

- 做法：`get_tenant_id` 是个 thin dependency，从请求上下文取 tenant_id，demo 下回退 `DEFAULT_TENANT_ID`，把租户喂给下游 `SET LOCAL`。
- 由此 RLS 的定位明确为**防应用 bug 的 defense-in-depth**（漏写 `WHERE tenant_id` 时数据库兜底），不假装防恶意客户端——这是诚实的边界。
- 现有 `tenant_id` 全链路（`ChatRequest.tenant_id`、`stream(tenant_id=...)`、`DEFAULT_TENANT_ID`、`IsolatedCheckpointer` 的 `user_tenant_id`）原样保留，M4 跨租户隔离等已交付功能不受影响。

---

## 5. RLS policy 草案

```sql
-- 4 张业务表（以 kb_documents 为例）：按 tenant_id 隔离
ALTER TABLE kb_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE kb_documents FORCE ROW LEVEL SECURITY;  -- owner 也受约束

CREATE POLICY tenant_isolation ON kb_documents
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

-- tenants 注册表：没有 tenant_id 列，按主键 id 隔离（只见自己那条）
ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenants FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_self ON tenants
    USING (id = current_setting('app.tenant_id', true));
```

```python
# core/db.py — 每请求事务注入租户上下文（fail-closed）
@asynccontextmanager
async def tenant_session(engine: AsyncEngine, tenant_id: str):
    async with engine.begin() as conn:
        # set_config(..., is_local=true) == SET LOCAL，但支持绑定参数（SET 不支持），
        # 注入安全；仅在本事务有效，连接归还池后自动清空。
        await conn.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_id},
        )
        yield conn
```

> `current_setting('app.tenant_id', true)` 的第二参 `true` = 未设置时返回 NULL 而非报错；NULL 与任何 `tenant_id` 比较为 false → **默认拒绝（fail-closed）**，符合安全语义。

---

## 6. 如何运行 / 验证

应用迁移（owner 角色）：

```bash
psql "$DATABASE_URL" -f infra/docker/migrations/0001_rls.sql
```

跑 RLS 隔离测试（需 live Postgres + 以低权限角色连库，否则自动 skip）：

```bash
APP_DATABASE_URL="postgresql+psycopg://resolveai_app:resolveai_app@localhost:5432/resolveai" \
  uv run python -m pytest apps/api/tests/test_rls_isolation.py -q
```

> 不设 `APP_DATABASE_URL`（即以超级用户 `resolveai` 连库）时这些测试会 **skip** 并提示——因为超级用户绕过 RLS，测不出隔离。这是设计内的诚实行为。

手动 smoke（**须以 `resolveai_app` 连库**，否则超级用户绕过 RLS 看不出效果）：

```sql
SET app.tenant_id = 'demo';
SELECT count(*) FROM kb_documents;          -- 只见 demo 行
SET app.tenant_id = 'acme';
SELECT count(*) FROM kb_documents;          -- 只见 acme 行（demo 不可见）
INSERT INTO kb_documents(tenant_id, title, content) VALUES ('demo', 'x', 'y');  -- WITH CHECK 拒绝
```

---

## 7. 风险 / 关键决策

- **决策 A — owner/superuser bypass（实施时已修正）**：原方案押注「`FORCE ROW LEVEL SECURITY` 让 `resolveai` owner 也受约束」，**但 live 验证发现 demo 的 `resolveai` 是 `POSTGRES_USER` 超级用户（`rolsuper=true`、`rolbypassrls=true`），超级用户 / BYPASSRLS 角色无条件绕过 RLS——`FORCE` 只对「非超级用户的表 owner」生效**。结果 3 项负向测试全失败（RLS 形同虚设）。修正：迁移里建低权限角色 `resolveai_app`（`NOSUPERUSER NOBYPASSRLS`），应用运行时以它连库（`APP_DATABASE_URL`，仅 KB 检索这条 tenant-scoped SQLAlchemy 路径），RLS 才真正生效；`resolveai` 留作迁移/seed/扩展/LangGraph setup。这正是原文标注「本项目不做」的「生产形态」——事实证明它不是可选项，而是 RLS 能落地的前提。诚实叙事：RLS 的隔离价值只在「非超级用户连库」时成立。
- **SET LOCAL 必须在事务内**：`engine.begin()` 包裹；`lru_cache` 的进程级连接池没问题，因为 `SET LOCAL` 随事务结束失效，不会泄漏到下个请求。严禁用 `SET`（非 LOCAL）污染池连接。
- **LangGraph checkpoint 不走 RLS**：其表无 tenant 列，隔离由 `IsolatedCheckpointer` 兜底。这是有意的边界，写进 blog / 文档而非假装覆盖。
- **pgvector HNSW + RLS**：RLS 在 plan 上加过滤谓词，HNSW 索引仍可用但召回数可能受 row filter 影响；M6 golden set 已 1.0，迁移后需重跑 `scripts/eval_retrieval.py` 确认无回归。
- **seed_db.py**：`FORCE RLS` 下 owner 也受 policy 约束，故需在 seed 事务里加 `SET app.tenant_id = :tenant`（用脚本现有的 `--tenant` 值），否则 `tenants` / `kb_documents` 的 `WITH CHECK` 会拒插入。

---

## 8. 验收

- [x] 4 张业务表 `ENABLE` + `FORCE` RLS + `tenant_isolation` policy；`tenants` 注册表单独 `tenant_self`（按 `id`）policy。
- [x] 低权限 `resolveai_app` 角色（`NOSUPERUSER NOBYPASSRLS`）+ 表/序列 grant，迁移内幂等创建（修正 Decision A）。
- [x] live PG 负向测试：tenant A 看不到 / 改不了（`WITH CHECK`）tenant B 行；未 set context → 0 行（fail-closed）。3 项以 `resolveai_app` 连库通过；以超级用户连库则诚实 skip。
- [x] `get_tenant_id` 就位：租户从请求上下文注入（无鉴权，demo 回退 `DEFAULT_TENANT_ID`）；`chat.py` 接 `Depends(get_tenant_id)`。
- [x] 现有 hermetic test suite 全绿（114 passed）；retrieval 集成测试在两种角色下均通过。
- [x] 文档化 LangGraph checkpoint 的 app 层隔离边界（无 tenant 列，不被 RLS 覆盖，由 `IsolatedCheckpointer` 兜底）。
- [x] 迁移后 `scripts/eval_retrieval.py` recall 回归确认：`hybrid` / `dense_only` 均为 `recall@5=1.000`（2026-05-31 live 验证，报告 `reports/retrieval_eval_20260531_093508.json`）。
