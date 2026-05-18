# QuantGPT Strategy Post-MVP Scope

This document is the PMVP0 launch audit for the strategy framework. The MVP
baseline is the commit chain ending at:

- `00b5d0a` MVP0 helper extraction
- `fc19c9c` MVP1-3 StrategySpec, adapter, validator
- `eac2944` MVP4-6 strategy pipeline, score, report
- `1feaf19` MVP7-8 MCP and REST strategy tools
- `47f6793` MVP9 strategy workflow docs
- `e97bab1` T22 market cache compatibility fix

## Authorized Post-MVP Scope

This run is authorized to complete the PMVP execution packages from
`docs/quantgpt_strategy_framework_plan.md`:

| Package | Deliverable |
|---|---|
| PMVP1 | `StrategySpecV1` with v0 compatibility and explicit schema versioning |
| PMVP2 | Multi-factor scoring plus `top_n` and score-weighted portfolio rules |
| PMVP3 | Standalone candidate signal export for JSON/CSV review |
| PMVP4 | Strategy diagnosis taxonomy and suggested spec-level fixes |
| PMVP5 | Strategy-level anti-overfit and rolling validation summaries |
| PMVP6 | One non-A-share demo market adapter with contract tests |
| PMVP7 | Strategy persistence models, migration, and REST access |
| PMVP8 | Frontend strategy workbench for template instantiation, spec validation, task submission, status tracking, result display, HTML report access, and candidate export |
| PMVP9 | Strategy templates, governance metadata, and candidate optimizer |

## Permanent Boundaries

- QuantGPT outputs candidate strategy specs, target weights, rebalance signals,
  diagnostics, validation summaries, reports, and audit metadata.
- It must not create broker integrations, real accounts, order tickets,
  execution instructions, automatic trading, or real-money workflows.
- WQ BRAIN remains an existing factor workflow and is not a strategy adapter,
  strategy validator, or Post-MVP acceptance path.

## Regression Gates

Run the following gates after each substantial package or package group:

```bash
.venv/bin/python -m pytest tests/test_strategy_spec.py tests/test_strategy_adapters.py tests/test_strategy_validator.py tests/test_strategy_portfolio.py tests/test_strategy_backtest.py tests/test_strategy_score.py tests/test_strategy_report.py tests/test_strategy_docs.py
.venv/bin/python -m pytest tests/test_mcp_strategy_tools.py tests/test_routes_strategy.py
.venv/bin/python -m pytest tests/test_backtest.py tests/test_market_data.py tests/test_iteration.py tests/test_routes_backtest.py
```

For frontend PMVP8:

```bash
cd frontend && npm run build
```

For code touched in each package, run scoped `ruff check` and
`git diff --check` before committing.
