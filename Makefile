.PHONY: setup run dev test test-collect test-smoke lint check check-local engine-check clean frontend

PYTHON := $(shell command -v python3 2>/dev/null || command -v python 2>/dev/null)
VENV := .venv
BIN := $(VENV)/bin

setup:
	@echo "==> Creating virtual environment..."
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -e ".[dev]"
	@if [ ! -f .env ]; then cp .env.example .env && echo "==> Created .env from template (edit as needed)"; fi
	@echo ""
	@echo "Setup complete! Run: make run"

run:
	$(BIN)/python -m quantgpt --transport http

dev:
	$(BIN)/python -m quantgpt --transport http --port 8003

test:
	$(BIN)/pytest tests/ -x -q

test-collect:
	$(BIN)/pytest --collect-only -q tests

test-smoke:
	$(BIN)/pytest -x -q \
		tests/test_auth.py \
		tests/test_task_store.py \
		tests/test_task_executor.py \
		tests/test_routes_backtest.py \
		tests/test_routes_strategy.py \
		tests/test_strategy_spec.py \
		tests/test_strategy_backtest.py \
		tests/test_wq_submission_guard.py

lint:
	$(BIN)/ruff check quantgpt/ tests/
	$(BIN)/pyright quantgpt/

check: lint test frontend engine-check

check-local: lint test frontend

engine-check:
	@if ! command -v cargo >/dev/null 2>&1; then \
		echo "cargo not found; unable to run Rust engine checks"; \
		exit 1; \
	fi; \
	cd engine && cargo check --all-targets && \
	cargo test

frontend:
	cd frontend && npm ci && npm run build

clean:
	rm -rf $(VENV) *.egg-info __pycache__ quantgpt.db
