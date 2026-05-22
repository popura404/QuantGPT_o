# Testing QuantGPT

This document defines the runnable test entry points for local development and CI.
The goal is to make each check explicit about what it validates and whether Rust
engine coverage is included.

## Setup

Create the Python environment and install development dependencies:

```bash
make setup
```

The setup command creates `.venv`, installs `.[dev]`, and copies `.env.example`
to `.env` when no local `.env` exists. Tests use local test configuration from
`tests/conftest.py`, including in-memory SQLite, `AUTH_DISABLED=false`, and the
thread task backend.

Install frontend dependencies through the build target when needed:

```bash
make frontend
```

Rust engine checks require a working `cargo` installation. If `cargo` is not
available, `make engine-check` and the full `make check` gate intentionally fail
instead of treating the engine as verified.

## Local Test Layers

Use the smallest layer that proves the change you made:

```bash
make test-collect
```

Validates pytest discovery and import-time wiring without executing tests.

```bash
make test-smoke
```

Runs a focused backend smoke suite across authentication, task execution,
backtest task routes, strategy routes/spec/backtest behavior, and WQ submission
guardrails.

```bash
make test
```

Runs the full Python pytest suite with fail-fast behavior.

```bash
make lint
```

Runs Ruff over `quantgpt/` and `tests/`, then Pyright over `quantgpt/`.

```bash
make check-local
```

Runs Python linting, the full Python suite, and the frontend production build.
This is the recommended local all-in gate on machines without Rust installed.

```bash
make check
```

Runs the complete gate: Python linting, Python tests, frontend build, and Rust
engine checks. This is the release/CI-equivalent command and requires `cargo`.

## Frontend

The frontend v1 gate is the TypeScript and Vite production build:

```bash
cd frontend && npm run build
```

Vite may report chunk-size or static/dynamic import warnings. Those warnings are
not currently blocking unless the command exits non-zero.

## Rust Engine

Run Rust checks when touching `engine/src/` or the Python Rust bridge:

```bash
make engine-check
```

The target runs:

```bash
cd engine && cargo check --all-targets && cargo test
```

If Rust is unavailable locally, report the engine path as unverified and rely on
CI or a Rust-enabled machine before closing engine changes.

## CI Alignment

GitHub Actions currently runs the same core gates in separate jobs:

- Ruff and Pyright for Python.
- `make engine-check` for the Rust engine.
- `pytest tests/ -x -q --cov=quantgpt` with coverage enforcement.
- `pip-audit` for direct dependency vulnerabilities.
- `cd frontend && npm ci && npm run build` for the React app.

Before merging broad backend, frontend, or engine changes, prefer `make check`
on a Rust-enabled machine. For Python/frontend-only changes on a machine without
Rust, run `make check-local` and document that Rust was not locally verified.
