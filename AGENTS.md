# Repository Guidelines

## Project Structure & Module Organization

QuantGPT is a Python backend with a Rust acceleration engine and a React frontend. Backend code lives in `quantgpt/`, with FastAPI routes in `quantgpt/routes/`, migrations in `quantgpt/migrations/`, and strategy modules in `quantgpt/strategy/`. Tests live in `tests/`. The Rust engine is under `engine/src/`. Frontend source is in `frontend/src/`, with API clients in `api/`, reusable UI in `components/`, hooks in `hooks/`, and route pages in `pages/`. Documentation and research notes are in `docs/`; example factor assets are in `example_factor/`.

## Build, Test, and Development Commands

- `make setup`: create `.venv`, install dev dependencies, and copy `.env.example` to `.env`.
- `make dev`: run the backend HTTP server on port `8003`.
- `make run`: run the default QuantGPT server.
- `make test`: run `pytest tests/ -x -q`.
- `make lint`: run Ruff and Pyright.
- `make frontend`: install frontend dependencies and build React.
- `cd frontend && npm run dev`: start the Vite frontend dev server.
- `cd frontend && npm run build`: type-check and build frontend assets.
- `cd engine && cargo test`: run Rust engine tests when touching `engine/src/`.

## Coding Style & Naming Conventions

Use Python 3.10+ with 4-space indentation and typed public interfaces. Ruff handles imports, pyupgrade, bugbear, and simplification rules with a 120-character line length. Prefer `snake_case` for Python modules, functions, and variables; `PascalCase` for classes and Pydantic/ORM models. React components use `PascalCase.tsx`; hooks use `useSomething.ts`.

## Testing Guidelines

Pytest is primary, with `pytest-asyncio` in strict mode and coverage targeting `quantgpt/`. Name tests `tests/test_<module>.py`, and add focused regressions for route, persistence, strategy, or engine changes. For frontend changes, run `npm run build`; for Rust changes, run `cargo test` from `engine/`.

## Commit & Pull Request Guidelines

Recent history uses milestone-style subjects such as `PMVP8: add frontend strategy workbench` and `T22: preserve market cache datetime contract`; `CONTRIBUTING.md` also accepts `feat:`, `fix:`, `chore:`, and `docs:` prefixes. Keep commits focused and imperative. Pull requests should describe the change, list verification commands, link issues or docs, include screenshots for UI changes, and update docs when public APIs, MCP tools, or strategy contracts change.

## Security & Configuration Tips

Do not commit `.env`, credentials, market-data tokens, or WQ BRAIN session secrets. Start from `.env.example`, keep local SQLite files and generated caches out of commits, and mock external services in tests.
