# Data Quality and Snapshots

Purpose: make formal results data-source aware and snapshot-traceable while
keeping free-source operation.

Factor REST/MCP and StrategySpec v1 may enable data quality:

```json
{
  "data_quality": true,
  "data_quality_mode": "filter",
  "adjustment": "qfq"
}
```

StrategySpec v1 uses the nested form:

```json
{
  "validation": {
    "data_quality": {
      "enabled": true,
      "mode": "filter",
      "adjustment": "qfq"
    }
  }
}
```

Results expose:

- `data_snapshot_id`
- `data_source`
- `data_source_metadata`
- `data_quality.data_snapshot_id`
- `oos_result.data_snapshot_id`

Free-source degradation:

- Missing ST or suspension metadata is a warning in `filter` mode.
- Strict mode may block when tradeability proof is unavailable.
- Missing historical constituent history is an approximate-universe risk.
- Missing `data_snapshot_id` blocks promotion/export.

Common failures:

- `DATA_QUALITY_NOT_RUN`: promotion proof lacks data-quality output.
- `DATA_QUALITY_FAILED`: strict checks failed.
- `DATA_SNAPSHOT_REQUIRED`: export or promotion lacks snapshot proof.

Safe usage note: paid SDKs are optional. CI and default local backtests must not
require paid credentials.
