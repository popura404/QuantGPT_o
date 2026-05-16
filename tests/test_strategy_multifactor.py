"""Post-MVP multi-factor strategy tests."""

import pandas as pd

from quantgpt.strategy.backtest import StrategyBacktestRequest, run_strategy_backtest
from quantgpt.strategy.spec import example_strategy_spec_v1


def _market_df():
    dates = pd.bdate_range("2024-01-02", periods=24)
    stocks = ["A", "B", "C", "D"]
    rows = []
    for idx, stock in enumerate(stocks):
        price = 10.0 + idx * 10
        for date in dates:
            rows.append({
                "trade_date": date,
                "stock_code": stock,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": 100 + idx * 100,
                "amount": price * (100 + idx * 100),
                "pct_change": 0.1 * idx,
            })
            price *= 1.001 + idx * 0.001
    return pd.DataFrame(rows)


def _request():
    spec = example_strategy_spec_v1()
    spec["universe"] = "small_scale"
    spec["factors"] = [
        {"id": "price_rank", "expression": "close", "direction": "higher_is_better", "weight": 0.6},
        {"id": "volume_low", "expression": "volume", "direction": "lower_is_better", "weight": 0.4},
    ]
    spec["signal_rules"] = {"type": "rank_threshold", "top_n": 2}
    spec["portfolio_rule"] = {"weighting": "score_weighted", "rebalance_period": 5}
    spec["risk_rules"]["max_asset_weight"] = 1.0
    spec["risk_rules"]["max_turnover"] = None
    return StrategyBacktestRequest.model_validate({
        "spec": spec,
        "start_date": "2024-01-02",
        "end_date": "2024-02-02",
        "benchmark": "hs300",
    })


def test_strategy_v1_backtest_combines_multiple_factor_directions():
    result = run_strategy_backtest(_request(), market_df=_market_df())

    assert result.spec.schema_version == "strategy_spec/v1"
    assert not result.strategy_returns.empty
    first_date = pd.to_datetime(result.target_weights["trade_date"]).min()
    first_weights = result.target_weights[result.target_weights["trade_date"] == first_date]
    assert set(first_weights["stock_code"]) == {"C", "D"}
    assert first_weights["target_weight"].nunique() > 1
