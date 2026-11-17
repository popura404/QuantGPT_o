# Testing QuantGPT

The supported development baseline is **CPython 3.12** on Windows x64 and Linux x64.
The package deliberately declares `>=3.12,<3.13`; other versions need a passing
matrix before support is claimed. MCP remains on its compatible 1.x API. Source
syntax remains Ruff-compatible with Python 3.10, which is not a support claim.

## Reproducible setup

Windows PowerShell, with CPython 3.12 installed:

```powershell
./scripts/setup.ps1
# Configure JWT_SECRET_KEY and QUANTGPT_ADMIN_PASSWORD in .env before the first HTTP start.
./scripts/dev.ps1
```

To select an interpreter or an alternate environment explicitly:

```powershell
./scripts/setup.ps1 -Python 'C:/path/to/python312/python.exe' -Venv .venv312
./scripts/dev.ps1 -Venv .venv312 -Port 8003
```

Linux:

```bash
make setup
# Configure the local .env before the first HTTP start.
make dev
```

Both install exact, SHA-256 checked platform locks and the editable project with
`--no-deps --no-build-isolation`. The build backend and wheel tool are included in
the lock. Setup copies `.env.example` only if `.env` does not exist and preserves
its authentication setting. Servers bind to loopback by default. Setup never
reuses an incompatible interpreter or deletes an old environment.

The checked-in locks are `requirements/windows-py312.lock` and
`requirements/linux-py312.lock`; `engine/Cargo.lock` and
`frontend/package-lock.json` pin the other runtimes. Optional PostgreSQL and Celery
extras are not covered by the Python baseline locks or claimed tested here.
To intentionally refresh Python dependencies, use uv 0.12.23:

```bash
uv pip compile pyproject.toml --extra dev --python-version 3.12 --python-platform x86_64-pc-windows-msvc --generate-hashes -o requirements/windows-py312.lock
uv pip compile pyproject.toml --extra dev --python-version 3.12 --python-platform x86_64-unknown-linux-gnu --generate-hashes -o requirements/linux-py312.lock
```

Review the resulting diff, install both environments in CI, and rerun the gates.
Cross-platform resolution by itself does not prove Linux execution.

## Python tests and lint

```powershell
$env:QUANTGPT_RUST_ENGINE = '0'
./.venv312/Scripts/python.exe -m pytest tests -q --tb=short
./.venv312/Scripts/python.exe -m ruff check quantgpt tests
./.venv312/Scripts/python.exe scripts/check_pyright_baseline.py
./.venv312/Scripts/python.exe -m pip check
```

If a previous sandbox owns the default pytest temporary/cache directory, use a
new test-only directory under the repository; do not delete or change permissions
on the old directory:

```powershell
./.venv312/Scripts/python.exe -m pytest tests -q --tb=short --basetemp=test-results/pytest-run-1 -o cache_dir=test-results/pytest-cache
```

Give concurrent test runs distinct `--basetemp` paths. Pytest owns and may clean
its selected basetemp, so never point it at user data. `test-results/` is ignored.

Linux equivalents are `make test-collect`, `make test-smoke`, `make test`, and
`make lint`. Tests use in-memory SQLite and mocked remote services through
`tests/conftest.py`. They must not require market credentials or perform a WQ
submission. CI adds `--cov=quantgpt --cov-report=term-missing --cov-fail-under=33`.
Coverage measures executed lines, not financial correctness or external-service
coverage.

## Type debt is explicit

The tracked baseline in `docs/testing/pyright-baseline.json` supplies reference
diagnostics for the locked Python 3.12 environment.

`check_pyright_baseline.py` compares exact repository-relative file, severity,
rule, message and multiplicity. Lines are recorded for navigation but are not
identity, so moving code does not conceal or invent debt. New diagnostics fail;
resolved diagnostics are reported. No additional Pyright rule category is disabled.
Use `--strict-path quantgpt/research` (repeatable) to require zero diagnostics in
changed modules. `make typecheck-full` or the following still reports all debt:

```powershell
./.venv312/Scripts/python.exe -m pyright --pythonpath ./.venv312/Scripts/python.exe quantgpt
```

`--update` is an explicit reviewer-controlled baseline regeneration command,
never a CI step or a way to accept newly introduced errors. G4 requires clearing
unexplained historical debt; a passing delta gate is not a clean full type check.

## Rust

The Python channel explicitly sets `QUANTGPT_RUST_ENGINE=0`. Installed extensions
must not silently enable unverified numerical semantics. The separate Rust CI
channel uses Python 3.12 with PyO3 0.24, builds the actual wheel, imports it, and
runs the bridge tests. Numerical trust still depends on P06 differential tests;
compilation alone is insufficient.

```powershell
$env:PYO3_PYTHON = (Resolve-Path .venv312/Scripts/python.exe).Path
cargo check --manifest-path engine/Cargo.toml --locked --all-targets
cargo test --manifest-path engine/Cargo.toml --locked
./.venv312/Scripts/python.exe -m maturin build --manifest-path engine/Cargo.toml --locked --release --out engine/target/wheels
```

Linux uses `make engine-check`. Do not bypass PyO3's interpreter compatibility
check. `make check-local` excludes Rust; `make check` requires it.

## Frontend and performance

```powershell
npm.cmd --prefix frontend ci
npm.cmd --prefix frontend run build
./.venv312/Scripts/python.exe scripts/benchmark_research.py
```

The frontend has a dependency-free Node test runner (using the installed
TypeScript transpiler) for task subscription, cancellation, session changes,
late responses and loading full task details from list summaries:

```bash
npm --prefix frontend test
```

After installing the locked Python environment and building the frontend, run the
browser suite with Playwright Chromium:

```bash
cd frontend
npx playwright install chromium
PLAYWRIGHT_PYTHON=../.venv312/bin/python npm run test:e2e
```

`PLAYWRIGHT_PYTHON` names one Python executable, not a command with extra flags.
On Windows it defaults to `../.venv312/Scripts/python.exe`; an installed Edge can
be selected without downloading Chromium:

```powershell
$env:PLAYWRIGHT_BROWSER_CHANNEL = 'msedge'
npm.cmd --prefix frontend run test:e2e
```

CI uses Node 24, the Linux Python 3.12 lock, Chromium with system dependencies,
`npm test`, the production build and all browser tests. The browser server binds
to loopback and creates a fresh SQLite database/snapshot directory per run. It
replaces the production lifespan, disables Rust acceleration, and enforces cache
only market access; fixtures use synthetic research data and do not start email,
market refresh or WQ schedulers. Failed browser runs upload traces, screenshots
and the JSON result report. Local Edge execution does not establish Linux
Chromium or a successful GitHub Actions run.

The benchmark is offline, deterministic synthetic data. It records input hash,
parser source hash, dependency versions, revision, dirty-state flag, p50/p95,
sampled peak RSS, panel reads/bytes, expression compilation/evaluation counts,
serialization time and summary size. Provider calls are zero by construction.
`cold_application` rereads the parquet file; it does **not** flush the OS cache.
`warm_panel` reuses input and compiled expressions but still evaluates expressions.
It is an expression microbenchmark, not proof of shared-service caching or an
end-to-end provider benchmark.

The service mode exercises a real isolated SQLite database, project membership,
snapshot registration, Python factor backtests, research-card persistence and
verified artifact reads on the fixed 20-security/180-session offline demo:

```powershell
./.venv312/Scripts/python.exe scripts/benchmark_research.py --mode service --repeats 3 --label p19-service-synthetic --output benchmark-results/service.json
```

It independently instruments panel reads and numerical calls: a cold three-factor
batch must load once/compute three times, and an identical retry must do neither.
All result identities and artifact hashes must remain identical. Changing fees
must produce a new identity and computation while preserving the original result.
These assertions also run once in CI. Raw snapshots/databases stay under ignored
`benchmark-results/`; the script never connects to the user's database. The
printed speed difference measures evidence reuse, not numerical engine speed.
Source changes during a run invalidate the benchmark and require a fresh run.

To benchmark a particular parser revision, supply its Git reference with
`--source-ref` in expression mode and store the output under `benchmark-results/`.
The `make clean` target prints cleanup guidance and leaves local files in place.
