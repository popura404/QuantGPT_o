"""Direction-policy regressions for factor backtests."""

import numpy as np
import pandas as pd
import pytest

from quantgpt.backtest import run_factor_backtest


@pytest.fixture
def low_value_wins_df():
    dates = pd.bdate_range("2024-01-02", periods=80)
    stocks = [f"S{i:02d}" for i in range(12)]
    rows = []
    prices = {stock: 10.0 + i * 2.0 for i, stock in enumerate(stocks)}
    for date in dates:
        for i, stock in enumerate(stocks):
            ret = 0.012 if i < 6 else -0.004
            prev = prices[stock]
            close = prev * (1 + ret)
            high = max(prev, close) * 1.01
            low = min(prev, close) * 0.99
            rows.append({
                "trade_date": date,
                "stock_code": stock,
                "open": prev,
                "high": high,
                "low": low,
                "close": close,
                "volume": 1_000_000 + i,
                "amount": (1_000_000 + i) * close,
                "pct_change": ret * 100,
            })
            prices[stock] = close
    return pd.DataFrame(rows)


def _low_value_factor(df: pd.DataFrame) -> pd.Series:
    return pd.Series(
        [-1.0 if int(stock[1:]) < 6 else 1.0 for stock in df["stock_code"]],
        index=df.index,
    )


def test_auto_full_preserves_legacy_flip(low_value_wins_df):
    result = run_factor_backtest(
        low_value_wins_df,
        precomputed_factor=_low_value_factor(low_value_wins_df),
        n_groups=3,
        holding_period=5,
        neutralize_industry=False,
        neutralize_cap=False,
    )

    assert result["flipped"] is True
    assert result["direction_mode"] == "auto_full"
    assert result["direction_source"] == "auto_full_deprecated"
    assert result["direction_basis"] == "cost_adjusted_group_mean"
    assert result["fixed_direction"] == -1
    assert result["direction_warning"]


def test_fixed_direction_one_does_not_auto_flip(low_value_wins_df):
    result = run_factor_backtest(
        low_value_wins_df,
        precomputed_factor=_low_value_factor(low_value_wins_df),
        n_groups=3,
        holding_period=5,
        neutralize_industry=False,
        neutralize_cap=False,
        direction_mode="fixed",
        fixed_direction=1,
    )

    assert result["flipped"] is False
    assert result["direction_source"] == "fixed"
    assert result["fixed_direction"] == 1
    assert result["direction_warning"] is None


def test_fixed_direction_minus_one_uses_low_values_as_top_group(low_value_wins_df):
    auto_result = run_factor_backtest(
        low_value_wins_df,
        precomputed_factor=_low_value_factor(low_value_wins_df),
        n_groups=3,
        holding_period=5,
        neutralize_industry=False,
        neutralize_cap=False,
    )
    fixed_result = run_factor_backtest(
        low_value_wins_df,
        precomputed_factor=_low_value_factor(low_value_wins_df),
        n_groups=3,
        holding_period=5,
        neutralize_industry=False,
        neutralize_cap=False,
        direction_mode="fixed",
        fixed_direction=-1,
    )

    assert fixed_result["flipped"] is True
    assert fixed_result["fixed_direction"] == -1
    assert np.sign(fixed_result["strategy_returns"].sum()) == np.sign(auto_result["strategy_returns"].sum())


@pytest.mark.parametrize(
    "kwargs,error",
    [
        ({"direction_mode": "fixed", "fixed_direction": 0}, "fixed_direction"),
        ({"direction_mode": "fixed", "fixed_direction": None}, "fixed_direction"),
        ({"direction_mode": "auto_full", "fixed_direction": 1}, "fixed_direction"),
        ({"direction_mode": "disabled"}, "direction_mode"),
    ],
)
def test_invalid_direction_combinations_raise(low_value_wins_df, kwargs, error):
    with pytest.raises(ValueError, match=error):
        run_factor_backtest(
            low_value_wins_df,
            precomputed_factor=_low_value_factor(low_value_wins_df),
            n_groups=3,
            holding_period=5,
            neutralize_industry=False,
            neutralize_cap=False,
            **kwargs,
        )


def test_direction_adjusted_ic_and_factor_artifact_follow_selected_direction(low_value_wins_df):
    result = run_factor_backtest(
        low_value_wins_df,
        precomputed_factor=_low_value_factor(low_value_wins_df),
        n_groups=3,
        holding_period=5,
        neutralize_industry=False,
        neutralize_cap=False,
        direction_mode="fixed",
        fixed_direction=-1,
    )

    assert result["raw_rank_ic_mean"] < 0
    assert result["direction_adjusted_rank_ic_mean"] > 0
    raw_factor = result["_factor_df"]["factor_value"].reset_index(drop=True)
    adjusted_factor = result["_direction_adjusted_factor_df"]["factor_value"].reset_index(drop=True)
    pd.testing.assert_series_equal(adjusted_factor, -raw_factor, check_names=False)
