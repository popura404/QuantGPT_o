# Strategy Signal Export

Purpose: define the non-execution candidate export format.

Canonical schema:

```json
{
  "schema_version": "strategy_signal.v1",
  "experiment_id": "exp_...",
  "factor_hash": "fh_...",
  "notice": "Candidate signal only. Not an order or automated trading instruction.",
  "validation_summary": {
    "oos_enabled": true,
    "direction_policy": "train_fixed",
    "promotion_gate_passed": true,
    "data_snapshot_id": "ds_..."
  },
  "signals": []
}
```

Export requires:

- promotion-ready `validation_provenance`
- `experiment_id`
- `factor_hash`
- `data_snapshot_id`
- recursive absence of execution fields

Forbidden fields include `broker`, `account`, `api_key`, `order`,
`order_type`, `execution`, `submit_order`, `buy_volume`, `sell_volume`, and
`order_price`.

Common failures:

- `strategy_signal.v1 requires experiment_id`
- `strategy_signal.v1 export requires data_snapshot_id`
- forbidden execution field error

Safe usage note: `rank`, `score`, and `target_weight` are candidate signals.
They are not orders.
