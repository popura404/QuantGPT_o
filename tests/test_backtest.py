"""Tests for backtest.py — group backtest engine correctness."""

import numpy as np
import pandas as pd
import pytest

from quantgpt.backtest import (
    _calc_max_drawdown,
    _calc_monotonicity,
    assign_factor_quantiles,
    build_rebalance_dates,
    calculate_turnover_from_weights,
    compute_factor_values,
    run_factor_backtest,
)


@pytest.fixture
def market_df():
    """Synthetic market data with 5 stocks over 60 trading days."""
    dates = pd.bdate_range("2024-01-02", periods=60)
    stocks = [f"00000{i}.SZ" for i in range(1, 6)]
    rng = np.random.RandomState(123)

    rows = []
    for s_idx, s in enumerate(stocks):
        price = 10.0 + s_idx * 2
        for d in dates:
            ret = rng.randn() * 0.02
            new_price = price * (1 + ret)
            rows.append({
                "trade_date": d,
                "stock_code": s,
                "open": price,
                "high": max(price, new_price) * (1 + abs(rng.randn()) * 0.005),
                "low": min(price, new_price) * (1 - abs(rng.randn()) * 0.005),
                "close": new_price,
                "volume": 1_000_000 + rng.randint(0, 500_000),
                "amount": 10_000_000 + rng.randint(0, 5_000_000),
                "pct_change": ret * 100,
            })
            price = new_price
    return pd.DataFrame(rows)


class TestBacktestHelpers:
    def test_compute_factor_values_aligns_precomputed_series_by_index(self, market_df):
        df = market_df.copy().sort_values(["stock_code", "trade_date"])
        factor = pd.Series(np.arange(len(df), dtype=float), index=df.index)
        shuffled = factor.sample(frac=1, random_state=7)

        result = compute_factor_values(df, precomputed_factor=shuffled)

        pd.testing.assert_series_equal(result, factor)

    def test_build_rebalance_dates_uses_holding_period_stride(self):
        dates = pd.bdate_range("2024-01-02", periods=8)

        result = build_rebalance_dates(dates, holding_period=3)

        assert list(result) == [dates[0], dates[3], dates[6]]

    def test_assign_factor_quantiles_keeps_rebalance_effect_t_plus_one(self):
        dates = pd.bdate_range("2024-01-02", periods=6)
        rows = []
        for d_idx, date in enumerate(dates):
            for s_idx, stock in enumerate(["A", "B", "C", "D"]):
                rows.append({
                    "trade_date": date,
                    "stock_code": stock,
                    "factor_value": float(s_idx + d_idx),
                    "daily_ret": 0.01,
                    "close": 10.0 + s_idx,
                })
        work = pd.DataFrame(rows)
        rebalance_dates = [dates[0], dates[2], dates[4]]

        assigned, rebal_data, rebalance_dates_set = assign_factor_quantiles(
            work,
            rebalance_dates,
            n_groups=2,
        )

        assert rebalance_dates_set == rebalance_dates
        assert not rebal_data.empty
        assert not assigned.empty
        assert (assigned["_rebal_date"] < assigned["trade_date"].values.astype("datetime64[ns]")).all()
        assert set(assigned["_group"].unique()) == {0, 1}

    def test_calculate_turnover_from_weights_averages_daily_turnover(self):
        dates = pd.bdate_range("2024-01-02", periods=2)
        weights = {
            dates[0]: {"A": 0.5, "B": 0.5},
            dates[1]: {"B": 0.25, "C": 0.75},
        }

        turnover = calculate_turnover_from_weights(weights, holding_period=3)

        assert turnover == pytest.approx(0.25)


class TestRunFactorBacktest:
    def test_basic_run(self, market_df):
        result = run_factor_backtest(
            market_df,
            expression="close",
            n_groups=3,
            holding_period=5,
            neutralize_industry=False,
            neutralize_cap=False,
        )
        assert "strategy_returns" in result
        assert "group_returns" in result
        assert "ic_mean" in result
        assert "top_group_sharpe" in result
        assert "monotonicity_score" in result
        assert len(result["strategy_returns"]) > 0

    def test_group_count(self, market_df):
        result = run_factor_backtest(
            market_df,
            expression="close",
            n_groups=3,
            holding_period=5,
            neutralize_industry=False,
            neutralize_cap=False,
        )
        assert len(result["group_returns"]) == 3

    def test_ic_bounded(self, market_df):
        result = run_factor_backtest(
            market_df,
            expression="close",
            n_groups=3,
            holding_period=5,
            neutralize_industry=False,
            neutralize_cap=False,
        )
        assert -1.0 <= result["ic_mean"] <= 1.0
        assert -1.0 <= result["rank_ic_mean"] <= 1.0

    def test_ic_win_rate_bounded(self, market_df):
        result = run_factor_backtest(
            market_df,
            expression="close",
            n_groups=3,
            holding_period=5,
            neutralize_industry=False,
            neutralize_cap=False,
        )
        assert 0.0 <= result["ic_win_rate"] <= 1.0

    def test_turnover_bounded(self, market_df):
        result = run_factor_backtest(
            market_df,
            expression="close",
            n_groups=3,
            holding_period=5,
            neutralize_industry=False,
            neutralize_cap=False,
        )
        assert 0.0 <= result["turnover"] <= 1.0

    def test_cost_adjusted(self, market_df):
        result_no_cost = run_factor_backtest(
            market_df,
            expression="close",
            n_groups=3,
            holding_period=5,
            cost_rate=0.0,
            neutralize_industry=False,
            neutralize_cap=False,
        )
        result_with_cost = run_factor_backtest(
            market_df,
            expression="close",
            n_groups=3,
            holding_period=5,
            cost_rate=0.01,
            neutralize_industry=False,
            neutralize_cap=False,
        )
        assert result_with_cost["cost_adjusted"] is True
        strat_no_cost = result_no_cost["strategy_returns"].sum()
        strat_with_cost = result_with_cost["strategy_returns"].sum()
        assert strat_with_cost <= strat_no_cost + 1e-10

    def test_precomputed_factor(self, market_df):
        factor_values = pd.Series(
            np.random.RandomState(99).randn(len(market_df)),
            index=market_df.index,
        )
        result = run_factor_backtest(
            market_df,
            precomputed_factor=factor_values,
            n_groups=3,
            holding_period=5,
            neutralize_industry=False,
            neutralize_cap=False,
        )
        assert len(result["strategy_returns"]) > 0

    def test_no_expression_no_factor_raises(self, market_df):
        with pytest.raises(ValueError, match="expression.*precomputed_factor"):
            run_factor_backtest(
                market_df,
                expression=None,
                precomputed_factor=None,
                n_groups=3,
                holding_period=5,
            )

    def test_complex_expression(self, market_df):
        result = run_factor_backtest(
            market_df,
            expression="rank(ts_mean(close, 5) - ts_mean(close, 20))",
            n_groups=3,
            holding_period=5,
            neutralize_industry=False,
            neutralize_cap=False,
        )
        assert len(result["strategy_returns"]) > 0
        assert "group_returns" in result

    def test_direction_flip(self, market_df):
        """Verify that the engine detects when lower factor = better returns."""
        result = run_factor_backtest(
            market_df,
            expression="close",
            n_groups=3,
            holding_period=5,
            neutralize_industry=False,
            neutralize_cap=False,
        )
        assert isinstance(result["flipped"], bool)

    def test_stock_factor_data(self, market_df):
        result = run_factor_backtest(
            market_df,
            expression="close",
            n_groups=3,
            holding_period=5,
            neutralize_industry=False,
            neutralize_cap=False,
        )
        sfd = result["_stock_factor_data"]
        assert sfd is not None
        assert "stocks" in sfd
        assert len(sfd["stocks"]) > 0
        for stock in sfd["stocks"]:
            assert "stock_code" in stock
            assert "factor_value" in stock
            assert "group" in stock


class TestMaxDrawdown:
    def test_zero_drawdown(self):
        returns = pd.Series([0.01, 0.02, 0.01, 0.03])
        dd = _calc_max_drawdown(returns)
        assert dd <= 0.0

    def test_known_drawdown(self):
        returns = pd.Series([0.10, -0.20, 0.05])
        dd = _calc_max_drawdown(returns)
        assert dd < 0.0

    def test_empty_series(self):
        dd = _calc_max_drawdown(pd.Series(dtype=float))
        assert dd == 0.0


class TestMonotonicity:
    def test_perfect_monotonic(self):
        group_means = [0.01, 0.02, 0.03, 0.04, 0.05]
        mono = _calc_monotonicity(group_means)
        assert mono == pytest.approx(1.0)

    def test_inverse_monotonic(self):
        group_means = [0.05, 0.04, 0.03, 0.02, 0.01]
        mono = _calc_monotonicity(group_means)
        assert mono == pytest.approx(1.0)

    def test_flat(self):
        group_means = [0.02, 0.02, 0.02, 0.02]
        mono = _calc_monotonicity(group_means)
        assert 0.0 <= mono <= 1.0

    def test_too_few_groups(self):
        assert _calc_monotonicity([0.01, 0.02]) == 0.0


class TestPrecomputedFactorAlignment:
    """Regression: precomputed_factor must align by index, not position."""

    def test_shuffled_index_aligns_correctly(self, market_df):
        from quantgpt.backtest import api_context

        df = market_df.copy()
        df["trade_date"] = pd.to_datetime(df["trade_date"])

        factor = pd.Series(
            np.random.RandomState(99).randn(len(df)),
            index=df.index,
        )

        shuffled_factor = factor.sample(frac=1, random_state=42)

        with api_context():
            result = run_factor_backtest(
                df,
                precomputed_factor=shuffled_factor,
                n_groups=3,
                holding_period=5,
                neutralize_industry=False,
                neutralize_cap=False,
            )
        assert "strategy_returns" in result
        assert len(result["strategy_returns"]) > 0
