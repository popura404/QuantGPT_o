"""Tests for rust_bridge.py — fallback path when Rust engine not installed."""

import numpy as np
import pandas as pd
import pytest

from quantgpt.rust_bridge import RUST_AVAILABLE, compute_metrics_rust, eval_factor_expression


@pytest.fixture
def market_df():
    """Multi-stock DataFrame for testing."""
    dates = pd.bdate_range("2024-01-02", periods=30)
    stocks = ["000001.SZ", "000002.SZ", "600000.SH"]
    rng = np.random.RandomState(42)
    rows = []
    for d in dates:
        for s in stocks:
            rows.append({
                "trade_date": d,
                "stock_code": s,
                "open": 10 + rng.randn(),
                "high": 11 + abs(rng.randn()),
                "low": 9 + abs(rng.randn()),
                "close": 10 + rng.randn(),
                "volume": 1_000_000 + rng.randint(0, 500_000),
                "amount": 10_000_000 + rng.randint(0, 5_000_000),
                "pct_change": rng.randn() * 2,
            })
    return pd.DataFrame(rows)


class TestEvalFactorExpression:
    def test_returns_series(self, market_df):
        result = eval_factor_expression(market_df, "rank(close)")
        assert isinstance(result, pd.Series)
        assert len(result) == len(market_df)

    def test_simple_expression(self, market_df):
        result = eval_factor_expression(market_df, "close")
        pd.testing.assert_series_equal(
            result.reset_index(drop=True),
            market_df["close"].reset_index(drop=True),
            check_names=False,
        )

    def test_complex_expression(self, market_df):
        result = eval_factor_expression(market_df, "ts_mean(close, 5) / ts_std(close, 5)")
        assert isinstance(result, pd.Series)
        assert len(result) == len(market_df)

    def test_preserves_index(self, market_df):
        df = market_df.copy()
        df.index = range(100, 100 + len(df))
        result = eval_factor_expression(df, "close")
        assert list(result.index) == list(df.index)

    def test_derived_returns_do_not_cross_stock_boundaries(self, monkeypatch):
        from quantgpt import rust_bridge

        class FakeEngine:
            @staticmethod
            def eval_expression(expression, columns, stock_offsets, date_offsets):
                assert stock_offsets == [(0, 3), (3, 6)]
                return columns["returns"]

        df = pd.DataFrame({
            "trade_date": pd.to_datetime([
                "2024-01-01", "2024-01-02", "2024-01-03",
                "2024-01-01", "2024-01-02", "2024-01-03",
            ]),
            "stock_code": ["A", "A", "A", "B", "B", "B"],
            "close": [10.0, 11.0, 12.1, 100.0, 90.0, 99.0],
        })
        monkeypatch.setattr(rust_bridge, "RUST_ENABLED", True)
        monkeypatch.setattr(rust_bridge, "_engine", FakeEngine())

        result = rust_bridge.eval_factor_expression(df, "returns")

        expected = pd.Series([np.nan, 0.1, 0.1, np.nan, -0.1, 0.1], index=df.index, name="factor_value")
        pd.testing.assert_series_equal(result, expected)


class TestComputeMetricsRust:
    @pytest.mark.skipif(RUST_AVAILABLE, reason="Tests fallback path only")
    def test_returns_empty_dict_without_rust(self):
        rets = pd.Series([0.01, -0.02, 0.015, -0.005, 0.008])
        result = compute_metrics_rust(rets)
        assert result == {}
