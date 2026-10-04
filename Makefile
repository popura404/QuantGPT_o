.PHONY: setup run dev test test-collect test-smoke lint typecheck typecheck-full check check-local engine-check frontend benchmark clean

PYTHON ?= python3.12
VENV ?= .venv312
BIN := $(VENV)/bin

# PowerShell equivalents: scripts/setup.ps1 and scripts/dev.ps1.
setup:
	$(PYTHON) -c 'import sys; assert sys.version_info[:2] == (3, 12), "CPython 3.12 is required"'
	$(PYTHON) -m venv $(VENV)
	$(BIN)/python -m pip install --require-hashes -r requirements/linux-py312.lock
	$(BIN)/python -m pip install --no-deps --no-build-isolation -e .
	@test -f .env || cp .env.example .env

run:
	$(BIN)/python -m quantgpt --transport http --host 127.0.0.1

dev:
	$(BIN)/python -m quantgpt --transport http --host 127.0.0.1 --port 8003

test:
	QUANTGPT_RUST_ENGINE=0 $(BIN)/python -m pytest tests/ -x -q

test-collect:
	$(BIN)/python -m pytest --collect-only -q tests

test-smoke:
	QUANTGPT_RUST_ENGINE=0 $(BIN)/python -m pytest -x -q \
		tests/test_auth.py tests/test_task_store.py tests/test_task_executor.py \
		tests/test_routes_backtest.py tests/test_routes_strategy.py \
		tests/test_strategy_spec.py tests/test_strategy_backtest.py tests/test_wq_submission_guard.py

typecheck:
	$(BIN)/python scripts/check_pyright_baseline.py

typecheck-full:
	$(BIN)/python -m pyright --pythonpath $(BIN)/python quantgpt/

lint:
	$(BIN)/python -m ruff check quantgpt/ tests/
	$(MAKE) typecheck

check: lint test frontend engine-check

check-local: lint test frontend

engine-check:
	PYO3_PYTHON=$(abspath $(BIN)/python) cargo check --manifest-path engine/Cargo.toml --locked --all-targets
	PYO3_PYTHON=$(abspath $(BIN)/python) cargo test --manifest-path engine/Cargo.toml --locked

frontend:
	cd frontend && npm ci && npm run build

benchmark:
	$(BIN)/python scripts/benchmark_research.py

# Keep user databases, reports, caches and environments: cleanup must be deliberate.
clean:
	@echo "Generated artifacts are ignored. Remove only explicitly selected paths; user data is never deleted by make."
