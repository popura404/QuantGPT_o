# P00 reproducible baseline evidence

Date: 2026-10-05 (Asia/Shanghai). Status: **in_review**, with Linux execution pending.
Source: `25bda5e`; P00 preserves the old checkout's Python 3.14 `.venv` and creates
an independent `.venv312` from the available CPython 3.12.14 runtime.

| Check | Actual result |
| --- | --- |
| Windows isolated install from SHA-256 locked requirements | Passed; editable project installed without dependency re-resolution |
| API and MCP imports | Passed, MCP 1.30.0 |
| `python -m pip check` | Passed |
| Original 694-test suite plus P00 platform-path assertion | 694 passed, 17 warnings, 126.02 seconds |
| Frontend `npm.cmd --prefix frontend run build` | Passed; existing Vite bundle/import warnings remain |
| Explicit `QUANTGPT_RUST_ENGINE=0` | `RUST_ENABLED` is false |
| `cargo check --all-targets` with Python 3.12 | Passed; 5 existing warnings |
| `cargo test` with Python 3.12 | 10 passed |
| Historical full Pyright | 649 errors individually captured, not clean |
| Linux lock | Resolved; Linux installation/execution not run locally |
| External integrations | not_run |

Versions: CPython 3.12.14 x64; pandas 3.0.6; numpy 2.5.3; FastAPI 0.142.2;
MCP 1.30.0; Pyright 1.1.414; Ruff 0.16.10; Cargo 1.98.1; Node 24.14.0;
npm 11.11.0. Exact Python artifacts are in the two platform locks. The runner's
latest patch release of Python 3.12 is the CI interpreter target; local evidence
specifically concerns 3.12.14.

The baseline suite used a local `git archive 25bda5e` source snapshot, with only
the `Path(...).parts` Windows assertion applied. This keeps ongoing product edits
out of the historical test measurement. The snapshot is under ignored `.tools/`.
The command was:

```powershell
$env:QUANTGPT_RUST_ENGINE = '0'
./.venv312/Scripts/python.exe -m pytest .tools/p00-baseline/tests -q --tb=short --basetemp=test-results/p00-history-tmp -o cache_dir=test-results/pytest-cache
./.venv312/Scripts/python.exe scripts/check_pyright_baseline.py --source-root .tools/p00-baseline --update
./.venv312/Scripts/python.exe scripts/benchmark_research.py --source-ref 25bda5e --label known-incorrect-pre-fix-baseline --output docs/testing/performance-baseline-2026-10-05.json
```

Initial current-tree testing hit 36 fixture errors because the host's default
`pytest-of-gzyou` temporary directory was inaccessible. A fresh workspace-local
`--basetemp` addressed the environment issue; no user directories were removed.
Subsequent integrated tests must be reported separately from the historical suite.

The exact diagnostic baseline identifies file/rule/message/severity/multiplicity.
No diagnostic from newly edited modules is accepted into it. Existing
`reportMissingImports` / `reportMissingModuleSource` settings are unchanged; no
additional category was suppressed. The tracked baseline is temporary type debt,
not a G4 waiver. CI gates newly introduced diagnostics.

Historical synthetic microbenchmark: 20 securities × 252 business dates,
5 expressions × 5 repeats per phase, fixed seed 20261005. Cold-application p50
0.0362s / p95 0.0693s, warm-panel p50 0.0301s / p95 0.0382s; sampled peak RSS
108,875,776 bytes. Source and data hashes, counts and limitations are in
[the machine-readable result](performance-baseline-2026-10-05.json). Concurrent
workload, hardware and OS caching affect wall time. These measurements contain
known old algorithm defects and are not correctness or financial evidence.

Do not mark P00/G0 verified until the configured Windows/Linux clean-install
matrix and the integrated regression gate pass. Rust compilation is verified
locally; Python/Rust semantic parity is a separate P06 requirement.
