-include .env
export DATABASE_URL REDIS_URL CURSOR_SECRET
PYTHON ?= .venv/bin/python
export PYTHONPATH := $(CURDIR)/apps/orchestrator/src:$(CURDIR)/apps/mcp-server/src:$(CURDIR)/packages/workflow-sdk/src
COMPOSE := docker compose --env-file .env -f infrastructure/compose/compose.yml
.PHONY: contracts test lint build deps migrate up down
contracts:
	npm --prefix packages/contracts run check
test:
	$(PYTHON) -m pytest tests/control_plane tests/test_workflow_integration.py apps/mcp-server/tests -q --tb=short
lint:
	$(PYTHON) -m ruff check --config infrastructure/control-plane/ruff.toml apps/orchestrator apps/mcp-server tests/control_plane tests/integration tests/test_workflow_integration.py
build:
	$(PYTHON) -m compileall -q apps/orchestrator apps/mcp-server
deps:
	$(COMPOSE) up -d --wait postgres redis
migrate:
	$(PYTHON) -m xmux_control.db.migrate
up:
	$(COMPOSE) up -d --build
down:
	$(COMPOSE) down
