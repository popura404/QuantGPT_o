"""Tests for quantgpt.market_data — pure logic and cache behavior."""

import sys
import tempfile
import types

import numpy as np
import pandas as pd
import pytest

from quantgpt.market_data import (
    BENCHMARK_CODES,
    UNIVERSES,
    MarketDataFetcher,
    _fetch_akshare,
    _from_rq_code,
    _to_rq_code,
    _transform_rq_to_schema,
    get_universe,
)

# ─── Code conversion ─────────────────────────────────────────────

class TestCodeConversion:
    def test_to_rq_sh(self):
        assert _to_rq_code("sh.600519") == "600519.XSHG"

    def test_to_rq_sz(self):
        assert _to_rq_code("sz.000001") == "000001.XSHE"

    def test_from_rq_sh(self):
        assert _from_rq_code("600519.XSHG") == "sh.600519"

    def test_from_rq_sz(self):
        assert _from_rq_code("000001.XSHE") == "sz.000001"

    def test_roundtrip(self):
        codes = ["sh.600519", "sz.000858", "sh.601318", "sz.300750"]
        for c in codes:
            assert _from_rq_code(_to_rq_code(c)) == c


# ─── Stock code normalization ────────────────────────────────────

class TestNormalize:
    def setup_method(self):
        self.f = MarketDataFetcher()

    def test_baostock_format_passthrough(self):
        assert self.f._normalize_stock_code("sh.600519") == "sh.600519"

    def test_reversed_dot_format(self):
        assert self.f._normalize_stock_code("600519.SH") == "sh.600519"
        assert self.f._normalize_stock_code("000001.SZ") == "sz.000001"

    def test_case_insensitive(self):
        assert self.f._normalize_stock_code("SH.600519") == "sh.600519"
        assert self.f._normalize_stock_code("SZ.000001") == "sz.000001"

    def test_no_dot_prefix(self):
        assert self.f._normalize_stock_code("sh600519") == "sh.600519"
        assert self.f._normalize_stock_code("sz000001") == "sz.000001"


# ─── Universe ────────────────────────────────────────────────────

class TestUniverse:
    def test_small_scale_static(self):
        codes = get_universe("small_scale")
        assert len(codes) == 5
        assert all(c.startswith(("sh.", "sz.")) for c in codes)

    def test_small_scale_matches_constant(self):
        assert get_universe("small_scale") == UNIVERSES["small_scale"]

    def test_unknown_universe_raises(self):
        with pytest.raises(ValueError, match="Unknown universe"):
            get_universe("nonexistent_pool")


# ─── Benchmark codes ─────────────────────────────────────────────

class TestBenchmarkCodes:
    def test_known_benchmarks(self):
        for name in ("hs300", "zz500", "csi500", "csi1000", "sz50"):
            assert name in BENCHMARK_CODES
            info = BENCHMARK_CODES[name]
            assert "baostock" in info
            assert "rqdatac" in info
            assert "name" in info

    def test_csi500_is_zz500_alias(self):
        assert BENCHMARK_CODES["csi500"]["baostock"] == BENCHMARK_CODES["zz500"]["baostock"]


# ─── Cache path ──────────────────────────────────────────────────

class TestCachePath:
    def test_cache_path_format(self):
        with tempfile.TemporaryDirectory() as td:
            f = MarketDataFetcher(cache_dir=td)
            path = f._cache_path("sh.600519")
            assert path.endswith("sh_600519.parquet")

    def test_different_codes_different_paths(self):
        with tempfile.TemporaryDirectory() as td:
            f = MarketDataFetcher(cache_dir=td)
            assert f._cache_path("sh.600519") != f._cache_path("sz.000001")


# ─── Cache load/save ─────────────────────────────────────────────

class TestCacheRoundtrip:
    def test_load_empty_returns_none(self):
        with tempfile.TemporaryDirectory() as td:
            f = MarketDataFetcher(cache_dir=td)
            assert f._load_cache("sh.600519") is None

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as td:
            f = MarketDataFetcher(cache_dir=td)
            df = pd.DataFrame({
                "trade_date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
                "stock_code": ["sh.600519", "sh.600519"],
                "close": [1800.0, 1810.0],
            })
            f._save_cache("sh.600519", df)
            loaded = f._load_cache("sh.600519")
            assert loaded is not None
            assert len(loaded) == 2
            assert loaded["trade_date"].dtype == "datetime64[ns]"

    def test_save_empty_is_noop(self):
        with tempfile.TemporaryDirectory() as td:
            f = MarketDataFetcher(cache_dir=td)
            f._save_cache("sh.600519", pd.DataFrame())
            assert f._load_cache("sh.600519") is None


def test_transform_rq_preserves_first_tier_trade_metadata():
    rq_df = pd.DataFrame(
        {
            "open": [10.0, 10.5],
            "high": [10.8, 10.5],
            "low": [9.9, 10.5],
            "close": [10.5, 10.5],
            "prev_close": [10.0, 10.5],
            "volume": [1000, 0],
            "total_turnover": [10_500.0, 0.0],
            "limit_up": [11.0, 11.55],
            "limit_down": [9.0, 9.45],
            "suspended": [False, True],
        },
        index=pd.Index(pd.to_datetime(["2024-01-02", "2024-01-03"]), name="date"),
    )

    out = _transform_rq_to_schema(rq_df, "sh.600519")

    assert {"pre_close", "limit_up", "limit_down", "suspended", "trade_status"}.issubset(out.columns)
    assert out.loc[out.index[0], "pre_close"] == 10.0
    assert bool(out.loc[out.index[1], "suspended"]) is True
    assert out.loc[out.index[1], "trade_status"] == 0


def test_fetch_akshare_derives_pct_change_when_provider_column_is_missing(monkeypatch):
    fake_akshare = types.SimpleNamespace(
        stock_zh_a_hist=lambda **_kwargs: pd.DataFrame(
            {
                "日期": ["2024-01-02", "2024-01-03"],
                "开盘": [10.0, 10.5],
                "最高": [10.2, 11.0],
                "最低": [9.9, 10.4],
                "收盘": [10.0, 11.0],
                "成交量": [1000, 1200],
                "成交额": [10_000.0, 13_200.0],
            }
        )
    )
    monkeypatch.setitem(sys.modules, "akshare", fake_akshare)

    out = _fetch_akshare("sh.600519", "2024-01-02", "2024-01-03")

    assert out is not None
    assert "pct_change" in out.columns
    assert "pre_close" in out.columns
    assert out["pct_change"].iloc[1] == pytest.approx(10.0)
    assert out["pre_close"].iloc[1] == pytest.approx(10.0)


# ─── Forward returns ─────────────────────────────────────────────

class TestForwardReturns:
    def test_single_stock(self):
        dates = pd.date_range("2024-01-01", periods=10, freq="B")
        prices = [100 + i for i in range(10)]
        df = pd.DataFrame({
            "stock_code": ["sh.600519"] * 10,
            "trade_date": dates,
            "close": prices,
        })
        f = MarketDataFetcher()
        result = f.calculate_forward_returns(df, periods=[1, 5])
        assert "fwd_ret_1d" in result.columns
        assert "fwd_ret_5d" in result.columns
        assert result["fwd_ret_1d"].iloc[0] == pytest.approx(1 / 100)
        assert pd.isna(result["fwd_ret_1d"].iloc[-1])

    def test_multiple_stocks_independent(self):
        dates = pd.date_range("2024-01-01", periods=5, freq="B")
        df = pd.DataFrame({
            "stock_code": ["sh.600519"] * 5 + ["sz.000001"] * 5,
            "trade_date": list(dates) * 2,
            "close": [100, 110, 120, 130, 140, 200, 210, 220, 230, 240],
        })
        f = MarketDataFetcher()
        result = f.calculate_forward_returns(df, periods=[1])
        stock_a = result[result["stock_code"] == "sh.600519"]
        stock_b = result[result["stock_code"] == "sz.000001"]
        assert stock_a["fwd_ret_1d"].iloc[0] == pytest.approx(10 / 100)
        assert stock_b["fwd_ret_1d"].iloc[0] == pytest.approx(10 / 200)


# ─── fetch_stocks with cache only ────────────────────────────────

class TestFetchStocksCacheOnly:
    def test_returns_cached_data(self):
        with tempfile.TemporaryDirectory() as td:
            f = MarketDataFetcher(cache_dir=td)
            dates = pd.date_range("2024-01-02", "2024-03-29", freq="B")
            df = pd.DataFrame({
                "trade_date": dates,
                "stock_code": ["sh.600519"] * len(dates),
                "close": np.random.uniform(1700, 1900, len(dates)),
                "volume": np.random.uniform(1e6, 5e6, len(dates)),
                "amount": np.random.uniform(1e9, 5e9, len(dates)),
            })
            f._save_cache("sh.600519", df)
            result = f.fetch_stocks(["sh.600519"], "2024-01-02", "2024-03-29")
            assert result is not None
            assert len(result) > 0
            assert "vwap" in result.columns

    def test_vwap_computed(self):
        with tempfile.TemporaryDirectory() as td:
            f = MarketDataFetcher(cache_dir=td)
            dates = pd.date_range("2024-01-02", "2024-01-31", freq="B")
            df = pd.DataFrame({
                "trade_date": dates,
                "stock_code": ["sh.600519"] * len(dates),
                "close": [1800.0] * len(dates),
                "volume": [1_000_000.0] * len(dates),
                "amount": [1_800_000_000.0] * len(dates),
            })
            f._save_cache("sh.600519", df)
            result = f.fetch_stocks(["sh.600519"], "2024-01-02", "2024-01-31")
            assert result is not None
            assert result["vwap"].iloc[0] == pytest.approx(1800.0)
