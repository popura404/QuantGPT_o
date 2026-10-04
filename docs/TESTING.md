# Testing QuantGPT

The supported development baseline is **CPython 3.12** on Windows x64 and Linux x64.
The package deliberately declares `>=3.12,<3.13`; other versions need a passing
matrix before support is claimed. MCP remains on its compatible 1.x API. Source
syntax remains Ruff-compatible with Python 3.10, which is not a support claim.

## Reproducible setup

Windows PowerShell, with CPython 3.12 installed:

```powershell
./scripts/setup.ps1
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
make dev
```

Both install exact, SHA-256 checked platform locks and the editable project with
`--no-deps --no-build-isolation`. The build backend and wheel tool are included in
the lock. Setup copies `.env.example` only if `.env` does not exist and preserves
its authentication setting. Servers bind to loopback by default. Setup never
reuses an incompatible interpreter or deletes an old environment. The existing
Python 3.14 `.venv` from the original audit can coexist with `.venv312`.

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

The original `25bda5e` code under the locked Python 3.12 environment has **649
Pyright errors**, individually stored in `docs/testing/pyright-baseline.json`.
This differs from the audit's 436 errors under Python 3.14 and is the measured
baseline, not a number of reproduced runtime defects.

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

The benchmark is offline, deterministic synthetic data. It records input hash,
parser source hash, dependency versions, revision, dirty-state flag, p50/p95,
sampled peak RSS, panel reads/bytes, expression compilation/evaluation counts,
serialization time and summary size. Provider calls are zero by construction.
`cold_application` rereads the parquet file; it does **not** flush the OS cache.
`warm_panel` reuses input and compiled expressions but still evaluates expressions.
It is an expression microbenchmark, not proof of shared-service caching or an
end-to-end provider benchmark.

The checked-in historical result in
`docs/testing/performance-baseline-2026-10-05.json` loads the exact parser source
from `25bda5e` using `--source-ref`. Its label is `known-incorrect-pre-fix-baseline`:
old values contain known semantic bugs and must never be an algorithm oracle.
P19 needs a new correctness-repaired baseline plus licensed frozen real data.
The proposed 500-stock, 10-year real workload remains unverified.

## Recorded status

See [P00 baseline evidence](testing/P00_BASELINE.md). Local Windows success does
not imply Linux CI, browser end-to-end, PostgreSQL, licensed US data, WQ external
integration, or Rust numerical parity was verified. The CI matrix is executable
configuration until an actual run supplies that evidence. Generated environments,
cache files, credentials, SQLite sidecars, test output and benchmark runs are
ignored. `make clean` never deletes user databases or research assets.
