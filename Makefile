.PHONY: help install dev api web seed db-migrate test red-team lint fmt typecheck clean \
	chaos regression-gate demo-assets demo-record obs

# Owner/superuser DSN for DDL (RLS migration). resolveai_app lacks DDL rights, so
# this must run as the owner. Override for a remote/CI DB: make db-migrate MIGRATE_DSN=...
MIGRATE_DSN ?= postgresql://resolveai:resolveai@localhost:5432/resolveai

help:
	@echo "Targets:"
	@echo "  install         安装 Python + Node 依赖"
	@echo "  dev             同时启动后端 (api) + 前端 (web)"
	@echo "  api             仅启动 FastAPI 后端"
	@echo "  web             仅启动 Next.js 前端"
	@echo "  seed            初始化 Postgres + 灌入 FAQ / 演示 ticket"
	@echo "  db-migrate      对已存在的库应用 RLS 迁移（新容器已自动烧入；存量 volume 用这个）"
	@echo "  test            跑 pytest + frontend lint"
	@echo "  red-team        跑 200 个 adversarial prompt"
	@echo "  chaos           5K mock ticket 并发压测（M8，默认 fake backend）"
	@echo "  regression-gate online regression 门禁（对比 baseline）"
	@echo "  demo-assets     生成 demo 用 metrics.html / trace.html"
	@echo "  demo-record     生成 assets 后用 Playwright 录制 demo 视频"
	@echo "  obs             启动本地 OTel collector（--profile obs）"
	@echo "  lint            ruff + eslint"
	@echo "  fmt             ruff format + prettier"
	@echo "  typecheck       mypy + tsc"

install:
	uv sync
	cd apps/web && npm install

dev:
	@echo "→ 后端 :8000   前端 :3000"
	@( $(MAKE) api & $(MAKE) web & wait )

api:
	# `python -m uvicorn` keeps --reload subprocess on the project interpreter;
	# bare `uvicorn` can pick system site-packages when VIRTUAL_ENV/PATH is stale.
	uv run python -m uvicorn resolveai_api.main:app --reload --host 0.0.0.0 --port 8000

web:
	cd apps/web && npm run dev

seed:
	uv run python scripts/seed_db.py

db-migrate:
	# Idempotent (DROP POLICY IF EXISTS + CREATE; ENABLE/FORCE re-runnable). Fresh
	# `docker compose up` already applies this via 02-rls.sql; use this for a volume
	# created before RLS existed, then ensure APP_DATABASE_URL points at resolveai_app.
	psql "$(MIGRATE_DSN)" -f infra/docker/migrations/0001_rls.sql

test:
	uv run python -m pytest -q
	cd apps/web && npm run lint

red-team:
	uv run python scripts/red_team.py

chaos:
	uv run python scripts/chaos_load.py --total 5000 --concurrency 200

regression-gate:
	uv run python scripts/regression_gate.py

demo-assets:
	uv run python scripts/render_metrics_page.py

demo-record: demo-assets
	# Drives /chat + the generated artifacts and writes apps/web/demo/output/*.webm.
	# Start `make dev` first (ideally API under LLM_BACKEND=fake) for the chat beats.
	cd apps/web && npm run demo:record

obs:
	docker compose --profile obs up

lint:
	uv run ruff check .
	cd apps/web && npm run lint

fmt:
	uv run ruff format .
	cd apps/web && npm run format || true

typecheck:
	uv run mypy apps/api packages
	cd apps/web && npx tsc --noEmit

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
	find . -type d -name .ruff_cache -exec rm -rf {} +
	find . -type d -name .mypy_cache -exec rm -rf {} +
