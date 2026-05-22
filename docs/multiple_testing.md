# Multiple Testing and Similarity

Purpose: prevent repeated Agent search from promoting a factor on one attractive
backtest.

The multiple-testing report uses trial counts:

```json
{
  "trial_counts": {
    "total_trials_in_project": 20,
    "trials_in_same_universe": 8,
    "trials_in_same_factor_family": 4
  }
}
```

The similarity report checks expression overlap and optional value/return
correlations. Duplicate or redundant factors emit
`DUPLICATED_OR_REDUNDANT_FACTOR`.

Promotion blockers include:

- `FAILED_FDR`
- `ROLLING_UNSTABLE`
- `TRIAL_COUNT_UNAVAILABLE`
- `DUPLICATED_OR_REDUNDANT_FACTOR`

MCP example:

```text
run_multiple_testing_check(p_value, trial_counts, family_p_values, experiment_id)
find_similar_factors(candidate_expression, reference_expression)
promote_experiment(experiment_id, provenance)
```

Safe usage note: selection-stage results can be tracked, but promotion should
include final OOS, data-quality proof, trial-aware statistics, and similarity.
