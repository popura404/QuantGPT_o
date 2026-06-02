# Agent Safe Workflow

Purpose: give Agents a reproducible path from expression to candidate export.

Recommended MCP flow:

```text
validate_expression(expression)
run_backtest(expression, oos_enabled=true, validation_stage="selection", submit_only=true)
get_mcp_task_status(task_id)
run_backtest(expression, oos_enabled=true, validation_stage="final", submit_only=true)
get_mcp_task_status(task_id)
run_multiple_testing_check(..., experiment_id=experiment_id)
find_similar_factors(...)
promote_experiment(experiment_id, provenance)
export_strategy_candidate(result)
```

Long-running tools that support this async pattern:

- `run_backtest`
- `score_factor`
- `compute_factor_values`
- `run_anti_overfit`
- `run_rolling_validation`

Use `cancel_mcp_task(task_id)` when the current direction should stop. Cancellation is cooperative: a task may
finish the current stock, batch, or remote data call before it reports `cancelled`.

Single-stock research flow:

```text
get_stock_history(stock_code, start_date, end_date)
check_market_cache(universe, start_date, end_date, stock_code)
```

Do not treat a single-stock question as permission to run full-universe
`compute_factor_values(csi500)` or `score_factor(csi500)`.

Result fields to preserve:

- `experiment_id`
- `factor_hash`
- `config_hash`
- `data_snapshot_id`
- `validation_provenance`
- `promotion_blockers`

Common stop conditions:

- biased direction mode
- missing OOS summary
- missing data-quality proof
- missing snapshot proof
- failed multiple-testing check
- duplicate-factor similarity
- `STOCK_CACHE_MISSING`
- `REMOTE_PREFETCH_REQUIRED`

Safe usage note: do not reinterpret `research_only` payloads as authorization
for candidate/export/submit boundaries.

Cancellation note: a user- or agent-cancelled task is not itself a factor failure and should not be recorded as a
reject/export blocker unless the completed metrics or error payload justify that conclusion.
