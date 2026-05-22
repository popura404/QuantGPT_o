# Unbiased Validation

Purpose: separate exploratory factor research from promotion-ready evidence.

Formal factor evidence requires OOS validation with train-fixed direction:

```json
{
  "oos_enabled": true,
  "validation_stage": "final",
  "direction_policy": "train_fixed"
}
```

`validation_stage="selection"` may rank candidates with train and validation
windows, but final-test metrics are withheld. `validation_stage="final"` exposes
the test window and is required before promotion or export.

Compatibility mode remains available:

```json
{
  "oos_enabled": false,
  "direction_mode": "auto_full"
}
```

That mode is always `research_only`. It returns blockers such as
`BIASED_DIRECTION_MODE`, `OOS_SUMMARY_REQUIRED`, and
`FINAL_TEST_REQUIRED_FOR_PROMOTION`.

Common failures:

- `INVALID_OOS_DIRECTION_OVERRIDE`: OOS requests cannot pass fixed direction.
- `OOS_SUMMARY_REQUIRED`: no formal train/valid/test payload was provided.
- `FINAL_TEST_REQUIRED_FOR_PROMOTION`: selection-stage output cannot promote.

Safe usage note: top-level compatibility metrics are for display only. Promotion,
WQ submission preflight, and strategy export must use validation provenance.

Agent example:

```text
1. validate_expression(expression)
2. run_backtest(expression, oos_enabled=true, validation_stage="selection")
3. run_backtest(expression, oos_enabled=true, validation_stage="final")
4. promote_experiment(experiment_id, provenance)
```
