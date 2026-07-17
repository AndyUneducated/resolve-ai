.PHONY: help install dev api web seed db-migrate test red-team lint fmt typecheck clean \
	chaos regression-gate demo-assets demo-record obs obs-down metrics \
	stack-up stack-down stack-logs smoke

# Full-stack compose file (M15 one-click deploy). Base file (docker-compose.yml)
# is pulled in via `include:`.
FULL_STACK ?= docker-compose.full.yml

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
	@echo "  regression-gate online regression 门禁（对比 baseline，含 mean_cost_usd 成本回归）"
	@echo "  demo-assets     生成 demo 用 metrics.html / trace.html"
	@echo "  demo-record     生成 assets 后用 Playwright 录制 demo 视频"
	@echo "  obs             启动可观测栈：OTel collector + Tempo + Prometheus + Grafana（--profile obs）"
	@echo "  obs-down        拆掉可观测栈"
	@echo "  metrics         curl 本地 /metrics（需先 make api）"
	@echo "  stack-up        一键起全栈（postgres + api + web，M15；首次会 build 镜像）"
	@echo "  stack-down      拆掉全栈"
	@echo "  stack-logs      跟随全栈日志"
	@echo "  smoke           对已起的全栈跑冒烟（health + web + chat 往返）"
	@echo "  lint            ruff + eslint"
	@echo "  fmt             ruff format + prettier"
	@echo "  typecheck       mypy（source + packages，与 CI 门禁同口径）+ tsc"

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
	# OTel collector (4317/4318) + Tempo (3200) + Prometheus (9090) + Grafana (3001).
	# Grafana is anonymous-admin at http://localhost:3001 with a pre-provisioned
	# ResolveAI dashboard; Prometheus scrapes the host API /metrics (run `make api`).
	docker compose --profile obs up

obs-down:
	docker compose --profile obs down

stack-up:
	# One-click full stack (M15): postgres + api + web, health-gated startup.
	# First run builds the api/web images (heavy). Defaults to LLM_BACKEND=fake so
	# it boots with zero model downloads — override for live inference:
	#   make stack-up LLM_BACKEND=ollama EMBEDDING_BACKEND=ollama
	docker compose -f $(FULL_STACK) up --build -d
	@echo "→ api  http://localhost:8000/docs"
	@echo "→ web  http://localhost:3000"
	@echo "  (optional KB seed — needs Ollama: docker compose -f $(FULL_STACK) --profile seed up seed)"

stack-down:
	docker compose -f $(FULL_STACK) down

stack-logs:
	docker compose -f $(FULL_STACK) logs -f

smoke:
	./scripts/smoke.sh

metrics:
	@curl -s http://localhost:8000/metrics | grep -E '^resolveai_' || \
		echo "no resolveai_* metrics yet (run some tickets), or API not up (make api)"

lint:
	uv run ruff check .
	cd apps/web && npm run lint

fmt:
	uv run ruff format .
	cd apps/web && npm run format || true

typecheck:
	# Same scope as the CI type gate (.github/workflows/ci.yml): source + packages
	# must be mypy-clean. Test files are out of scope for now (see M15 plan).
	uv run mypy apps/api/src packages
	cd apps/web && npx tsc --noEmit

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
	find . -type d -name .ruff_cache -exec rm -rf {} +
	find . -type d -name .mypy_cache -exec rm -rf {} +
