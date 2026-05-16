# StrategySpec v0

`StrategySpecV0` is the MVP executable strategy contract. It describes a
candidate strategy for validation and backtesting; it is not Python strategy
code and it is not an order or brokerage instruction.

## Scope

- `schema_version`: `strategy_spec/v0`
- `asset_class`: `equity`
- `market`: `a_share`
- `frequency`: `daily`
- one factor only
- `direction`: `higher_is_better` or `lower_is_better`
- signal rule: `rank_threshold`
- portfolio rule: `equal_weight`
- risk rules: no shorting, max asset weight, optional max turnover
- output: HTML report and summary JSON; no standalone SignalExport in MVP

## JSON Example

```json
{
  "schema_version": "strategy_spec/v0",
  "name": "simple_momentum_top_quantile",
  "asset_class": "equity",
  "market": "a_share",
  "frequency": "daily",
  "universe": "hs300",
  "factors": [
    {
      "id": "momentum_20d",
      "expression": "rank(close / ts_mean(close, 20))",
      "direction": "higher_is_better",
      "weight": 1.0
    }
  ],
  "signal_rules": {
    "type": "rank_threshold",
    "long_quantile": 0.2
  },
  "portfolio_rule": {
    "weighting": "equal_weight",
    "rebalance_period": 5
  },
  "risk_rules": {
    "allow_short": false,
    "max_asset_weight": 0.05,
    "max_turnover": 0.8
  },
  "cost_model": {
    "type": "fixed_bps",
    "bps": 30
  },
  "validation": {
    "min_history_days": 252,
    "run_strategy_anti_overfit": false,
    "run_strategy_rolling_validation": false
  },
  "outputs": {
    "report": true,
    "signal_export": false
  }
}
```

## YAML Example

```yaml
schema_version: strategy_spec/v0
name: simple_momentum_top_quantile
asset_class: equity
market: a_share
frequency: daily
universe: hs300
factors:
  - id: momentum_20d
    expression: rank(close / ts_mean(close, 20))
    direction: higher_is_better
    weight: 1.0
signal_rules:
  type: rank_threshold
  long_quantile: 0.2
portfolio_rule:
  weighting: equal_weight
  rebalance_period: 5
risk_rules:
  allow_short: false
  max_asset_weight: 0.05
  max_turnover: 0.8
cost_model:
  type: fixed_bps
  bps: 30
validation:
  min_history_days: 252
  run_strategy_anti_overfit: false
  run_strategy_rolling_validation: false
outputs:
  report: true
  signal_export: false
```

YAML is provided for human editing only. The automated MVP validation path uses
JSON/Pydantic input.

## StrategySpec v1 JSON Example

`StrategySpecV1` is the Post-MVP extension. It keeps v0 valid while adding
multi-factor, `top_n`, `score_weighted`, SignalExport, diagnosis, and
strategy-level validation support.

```json
{
  "schema_version": "strategy_spec/v1",
  "name": "multi_factor_top_n_score_weighted",
  "asset_class": "equity",
  "market": "a_share",
  "frequency": "daily",
  "universe": "hs300",
  "factors": [
    {
      "id": "momentum_20d",
      "expression": "rank(close / ts_mean(close, 20))",
      "direction": "higher_is_better",
      "weight": 0.6
    },
    {
      "id": "reversal_5d",
      "expression": "rank(ts_delta(close, 5))",
      "direction": "lower_is_better",
      "weight": 0.4
    }
  ],
  "signal_rules": {
    "type": "rank_threshold",
    "top_n": 20
  },
  "portfolio_rule": {
    "weighting": "score_weighted",
    "rebalance_period": 5
  },
  "risk_rules": {
    "allow_short": false,
    "max_asset_weight": 0.05,
    "max_turnover": 0.8
  },
  "cost_model": {
    "type": "fixed_bps",
    "bps": 30
  },
  "validation": {
    "min_history_days": 252,
    "run_strategy_anti_overfit": true,
    "run_strategy_rolling_validation": true
  },
  "outputs": {
    "report": true,
    "signal_export": true
  }
}
```

Post-MVP still does not permit broker, account, order, API key, execution, or
real-money workflow fields.

## Backtest Request Example

```json
{
  "spec": {
    "schema_version": "strategy_spec/v0",
    "name": "simple_momentum_top_quantile",
    "asset_class": "equity",
    "market": "a_share",
    "frequency": "daily",
    "universe": "hs300",
    "factors": [
      {
        "id": "momentum_20d",
        "expression": "rank(close / ts_mean(close, 20))",
        "direction": "higher_is_better",
        "weight": 1.0
      }
    ],
    "signal_rules": { "type": "rank_threshold", "long_quantile": 0.2 },
    "portfolio_rule": { "weighting": "equal_weight", "rebalance_period": 5 },
    "risk_rules": { "allow_short": false, "max_asset_weight": 0.05, "max_turnover": 0.8 },
    "cost_model": { "type": "fixed_bps", "bps": 30 },
    "validation": {
      "min_history_days": 252,
      "run_strategy_anti_overfit": false,
      "run_strategy_rolling_validation": false
    },
    "outputs": { "report": true, "signal_export": false }
  },
  "start_date": "2024-01-02",
  "end_date": "2024-03-29",
  "benchmark": "hs300"
}
```

`benchmark` belongs to the backtest/report request. It is not a
`StrategySpecV0` identity field.

## Rejected MVP Inputs

- unknown fields, including `python_code`, `broker`, `account`, `order`, `api_key`, and `callback_url`
- markets other than `a_share`
- more than one factor
- missing `direction`
- `factor.weight` other than `1.0`
- `portfolio_rule.weighting` other than `equal_weight`
- `outputs.signal_export=true`
- strategy anti-overfit or rolling validation flags set to `true`

Example validation error payload:

```json
{
  "is_valid": false,
  "issues": [
    {
      "code": "MARKET_UNSUPPORTED",
      "path": "market",
      "message": "Input should be 'a_share'",
      "hint": "MVP only supports market='a_share'."
    }
  ]
}
```

## MCP and REST Entrypoints

MCP tools:

- `list_markets`
- `list_data_fields`
- `validate_strategy_spec`
- `run_strategy_backtest`
- `score_strategy`
- `generate_strategy_report`

REST endpoints:

- `GET /api/v1/strategy/markets`
- `GET /api/v1/strategy/data-fields?market=a_share`
- `GET /api/v1/strategy/templates`
- `POST /api/v1/strategy/templates/{template_id}/instantiate`
- `POST /api/v1/strategy/validate`
- `POST /api/v1/strategy/backtest`
- `POST /api/v1/strategy/export`
- `POST /api/v1/strategy/diagnose`
- `POST /api/v1/strategy/anti-overfit`
- `POST /api/v1/strategy/rolling-validation`
- `POST /api/v1/strategy/optimize`
- `POST /api/v1/strategy/specs`
- `POST /api/v1/strategy/runs`

REST v0 does not add separate score/report endpoints. The async backtest task
result contains `strategy_result`, `strategy_score`, `summary_json`, and
`report_url`.

## MVP Non-Goals

The following items are Post-MVP or permanent non-goals; they are not required
for the `StrategySpecV0` closeout.

- no multi-factor `StrategySpec`
- no `top_n` or weighted portfolio rule
- no non-A-share market adapter
- no standalone SignalExport endpoint
- no strategy-level anti-overfit or rolling validation execution
- no strategy persistence table or frontend strategy workbench
- no broker, account, order, trade execution, or real-money capability

## Validation

Run:

```bash
pytest tests/test_strategy_spec.py tests/test_strategy_adapters.py tests/test_strategy_validator.py
```

MVP closeout also runs:

```bash
pytest tests/test_strategy_portfolio.py tests/test_strategy_backtest.py tests/test_strategy_score.py tests/test_strategy_report.py
pytest tests/test_mcp_strategy_tools.py tests/test_routes_strategy.py
pytest tests/test_backtest.py tests/test_market_data.py tests/test_iteration.py tests/test_routes_backtest.py
```
