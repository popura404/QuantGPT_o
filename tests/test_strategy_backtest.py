"""Strategy backtest pipeline tests."""

import pandas as pd
import pytest
from pydantic import ValidationError

from quantgpt.strategy.backtest import StrategyBacktestRequest, run_strategy_backtest
from quantgpt.strategy.spec import example_strategy_spec


def _market_df():
    dates = pd.bdate_range("2024-01-02", periods=24)
    stocks = ["A", "B", "C", "D"]
    rows = []
    for idx, stock in enumerate(stocks):
        price = 10.0 + idx * 10
        daily_ret = 0.004 if stock in {"C", "D"} else -0.001
        for date in dates:
            price *= 1 + daily_ret
            rows.append({
                "trade_date": date,
                "stock_code": stock,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": 1000,
                "amount": price * 1000,
                "pct_change": daily_ret * 100,
            })
    return pd.DataFrame(rows)


def _request(cost_bps=0, direction="higher_is_better"):
    spec = example_strategy_spec()
    spec["universe"] = "small_scale"
    spec["factors"][0]["expression"] = "close"
    spec["factors"][0]["direction"] = direction
    spec["signal_rules"]["long_quantile"] = 0.5
    spec["portfolio_rule"]["rebalance_period"] = 5
    spec["risk_rules"]["max_asset_weight"] = 1.0
    spec["risk_rules"]["max_turnover"] = None
    spec["cost_model"]["bps"] = cost_bps
    return StrategyBacktestRequest.model_validate({
        "spec": spec,
        "start_date": "2024-01-02",
        "end_date": "2024-02-02",
        "benchmark": "hs300",
    })


def test_strategy_backtest_outputs_target_weights_and_returns():
    result = run_strategy_backtest(_request(), market_df=_market_df())

    assert not result.strategy_returns.empty
    assert not result.target_weights.empty
    assert not result.turnover_by_rebalance.empty
    assert result.latest_holdings
    assert {"strategy_returns", "target_weights"} - set(result.to_summary()) == {"strategy_returns", "target_weights"}


def test_strategy_backtest_uses_declared_direction_without_flipping():
    high = run_strategy_backtest(_request(direction="higher_is_better"), market_df=_market_df())
    low = run_strategy_backtest(_request(direction="lower_is_better"), market_df=_market_df())

    assert high.strategy_returns.mean() > low.strategy_returns.mean()
    assert high.diagnostics["factor_flipped_observed"] is False


def test_strategy_backtest_cost_reduces_returns():
    no_cost = run_strategy_backtest(_request(cost_bps=0), market_df=_market_df())
    with_cost = run_strategy_backtest(_request(cost_bps=100), market_df=_market_df())

    assert with_cost.strategy_returns.sum() <= no_cost.strategy_returns.sum()


def test_strategy_backtest_avoids_same_day_lookahead():
    result = run_strategy_backtest(_request(), market_df=_market_df())

    first_rebalance = pd.to_datetime(result.target_weights["trade_date"]).min()
    assert result.strategy_returns.index.min() > first_rebalance


def test_strategy_backtest_rejects_invalid_spec():
    data = _request().model_dump()
    data["spec"]["factors"][0]["direction"] = "sideways"

    with pytest.raises(ValidationError):
        StrategyBacktestRequest.model_validate(data)
