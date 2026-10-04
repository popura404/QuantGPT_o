# Strategy Signal Export

## Current server-owned export

新研究导出为 `strategy_signal.v2`，输入只有已授权项目和 `strategy_run_id`。服务端重算完整策略身份、读取实际 signal artifact（含因子值、score、eligibility）并检查 local_strategy profile。页面权重、caller-provided validation booleans 和旧 `promotion_ready` 字典不构成证明。证据不足返回 `exported:false` 与所需检查。以下 v1 格式保留为历史读取说明；旧 payload 导出 API 现返回 `SERVER_STRATEGY_RUN_REQUIRED`。新格式仍不包含券商、账户或真实订单指令。

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
