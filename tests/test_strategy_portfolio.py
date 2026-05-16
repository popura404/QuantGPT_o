"""Strategy signal, portfolio, and risk tests."""

import pandas as pd
import pytest

from quantgpt.strategy.portfolio import build_equal_weight_portfolio
from quantgpt.strategy.risk import apply_risk_rules
from quantgpt.strategy.signals import build_rank_threshold_signals
from quantgpt.strategy.spec import StrategySpecV0, example_strategy_spec


def _spec(direction="higher_is_better", max_asset_weight=1.0, max_turnover=None):
    data = example_strategy_spec()
    data["factors"][0]["direction"] = direction
    data["signal_rules"]["long_quantile"] = 0.5
    data["risk_rules"]["max_asset_weight"] = max_asset_weight
    data["risk_rules"]["max_turnover"] = max_turnover
    return StrategySpecV0.model_validate(data)


def _factor_frame():
    return pd.DataFrame({
        "trade_date": pd.to_datetime(["2024-01-02"] * 4),
        "stock_code": ["A", "B", "C", "D"],
        "factor_value": [1.0, 2.0, 3.0, 4.0],
    })


def test_signal_direction_selects_opposite_assets():
    high = build_rank_threshold_signals(_factor_frame(), _spec("higher_is_better"))
    low = build_rank_threshold_signals(_factor_frame(), _spec("lower_is_better"))

    assert set(high[high["eligibility"]]["stock_code"]) == {"C", "D"}
    assert set(low[low["eligibility"]]["stock_code"]) == {"A", "B"}


def test_equal_weight_portfolio_sums_to_one():
    signals = build_rank_threshold_signals(_factor_frame(), _spec())
    weights = build_equal_weight_portfolio(signals, _spec())

    assert weights["target_weight"].sum() == pytest.approx(1.0)
    assert set(weights["target_weight"]) == {0.5}


def test_max_asset_weight_clips_to_cash():
    signals = build_rank_threshold_signals(_factor_frame(), _spec(max_asset_weight=0.3))
    weights = build_equal_weight_portfolio(signals, _spec(max_asset_weight=0.3))
    result = apply_risk_rules(weights, _spec(max_asset_weight=0.3))

    assert result.target_weights["target_weight"].max() == pytest.approx(0.3)
    assert result.cash_weights.iloc[0]["cash_weight"] == pytest.approx(0.4)
    assert any(log["code"] == "MAX_ASSET_WEIGHT_CLIPPED" for log in result.risk_logs)


def test_turnover_limit_skips_rebalance():
    weights = pd.DataFrame({
        "trade_date": pd.to_datetime(["2024-01-02", "2024-01-02", "2024-01-09", "2024-01-09"]),
        "stock_code": ["A", "B", "C", "D"],
        "target_weight": [0.5, 0.5, 0.5, 0.5],
    })

    result = apply_risk_rules(weights, _spec(max_turnover=0.1))

    second = result.target_weights[result.target_weights["trade_date"] == pd.Timestamp("2024-01-09")]
    assert set(second["stock_code"]) == {"A", "B"}
    assert any(log["code"] == "TURNOVER_LIMIT_REBALANCE_SKIPPED" for log in result.risk_logs)
