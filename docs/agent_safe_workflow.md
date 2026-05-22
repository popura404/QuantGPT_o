# Agent Safe Workflow

Purpose: give Agents a reproducible path from expression to candidate export.

Recommended MCP flow:

```text
validate_expression(expression)
run_backtest(expression, oos_enabled=true, validation_stage="selection")
run_backtest(expression, oos_enabled=true, validation_stage="final")
run_multiple_testing_check(..., experiment_id=experiment_id)
find_similar_factors(...)
promote_experiment(experiment_id, provenance)
export_strategy_candidate(result)
```

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

Safe usage note: do not reinterpret `research_only` payloads as authorization
for candidate/export/submit boundaries.
