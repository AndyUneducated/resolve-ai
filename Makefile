.PHONY: help install dev api web seed test red-team lint fmt typecheck clean

help:
	@echo "Targets:"
	@echo "  install     安装 Python + Node 依赖"
	@echo "  dev         同时启动后端 (api) + 前端 (web)"
	@echo "  api         仅启动 FastAPI 后端"
	@echo "  web         仅启动 Next.js 前端"
	@echo "  seed        初始化 Postgres + 灌入 FAQ / 演示 ticket"
	@echo "  test        跑 pytest + frontend lint"
	@echo "  red-team    跑 200 个 adversarial prompt"
	@echo "  lint        ruff + eslint"
	@echo "  fmt         ruff format + prettier"
	@echo "  typecheck   mypy + tsc"

install:
	uv sync
	cd apps/web && npm install

dev:
	@echo "→ 后端 :8000   前端 :3000"
	@( $(MAKE) api & $(MAKE) web & wait )

api:
	uv run uvicorn resolveai_api.main:app --reload --host 0.0.0.0 --port 8000

web:
	cd apps/web && npm run dev

seed:
	uv run python scripts/seed_db.py

test:
	uv run pytest -q
	cd apps/web && npm run lint

red-team:
	uv run python scripts/red_team.py

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
