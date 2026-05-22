# Experiment Ledger

Purpose: make Agent research attempts reproducible and queryable, including
failures.

Core identifiers:

- `experiment_id`: unique run identifier.
- `factor_hash`: deterministic hash of normalized expression and research
  configuration.
- `config_hash`: deterministic hash of non-expression configuration.
- `data_snapshot_id`: deterministic market-data proof used by the run.

Lifecycle states include `parse_failed`, `data_quality_failed`,
`backtested_train`, `validated_oos`, `multiple_testing_checked`, `candidate`,
`rejected`, and `exported`.

MCP writes ledger rows for factor validation/backtest/scoring, diagnosis,
anti-overfit, rolling validation, multiple-testing, promotion/rejection, and
strategy-signal export events. Query/report tools include `list_experiments`,
`get_experiment`, `compare_experiments`, `show_factor_lineage`,
`summarize_trial_counts`, and `export_experiment_report`.

Example:

```json
{
  "experiment_id": "exp_...",
  "factor_hash": "fh_...",
  "status": "validated_oos",
  "data_snapshot_id": "ds_..."
}
```

Common failures:

- `EXPERIMENT_NOT_FOUND`: query or promotion target is absent.
- Illegal status transition: terminal experiments cannot be promoted later.

Safe usage note: old task rows remain readable. The ledger captures new attempts
prospectively and can support a later backfill for legacy rows.
