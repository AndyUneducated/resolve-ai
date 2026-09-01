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
	@echo "  install         Install Python + Node dependencies"
	@echo "  dev             Start the backend (api) + frontend (web) together"
	@echo "  api             Start only the FastAPI backend"
	@echo "  web             Start only the Next.js frontend"
	@echo "  seed            Initialize Postgres + seed FAQ / demo tickets"
	@echo "  db-migrate      Apply the RLS migration to an existing database (automatic for new containers; use this for existing volumes)"
	@echo "  test            Run pytest + frontend lint"
	@echo "  red-team        Run 200 adversarial prompts"
	@echo "  chaos           Load-test 5K concurrent mock tickets (M8, fake backend by default)"
	@echo "  regression-gate Online regression gate (compares baseline, including mean_cost_usd cost regression)"
	@echo "  demo-assets     Generate metrics.html / trace.html for the demo"
	@echo "  demo-record     Generate assets, then record the demo video with Playwright"
	@echo "  obs             Start the observability stack: OTel collector + Tempo + Prometheus + Grafana (--profile obs)"
	@echo "  obs-down        Tear down the observability stack"
	@echo "  metrics         curl the local /metrics endpoint (run make api first)"
	@echo "  stack-up        Start the full stack (postgres + api + web, M15; builds images on first run)"
	@echo "  stack-down      Tear down the full stack"
	@echo "  stack-logs      Follow full-stack logs"
	@echo "  smoke           Run smoke tests against the active stack (health + web + chat round trip)"
	@echo "  lint            ruff + eslint"
	@echo "  fmt             ruff format + prettier"
	@echo "  typecheck       mypy (source + packages, matching the CI gate) + tsc"

install:
	uv sync
	cd apps/web && npm install

dev:
	@echo "→ backend :8000   frontend :3000"
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
