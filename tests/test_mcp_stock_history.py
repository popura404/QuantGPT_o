"""MCP single-stock local cache tools."""

import json

import pandas as pd

from quantgpt import mcp_server
from quantgpt.market_data import MarketDataFetcher


def _cache_stock(tmp_path, monkeypatch, dates=None):
    class TmpFetcher(MarketDataFetcher):
        def __init__(self):
            super().__init__(cache_dir=str(tmp_path))

    monkeypatch.setattr(mcp_server, "MarketDataFetcher", TmpFetcher)
    fetcher = TmpFetcher()
    dates = pd.to_datetime(dates or ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"])
    df = pd.DataFrame({
        "trade_date": dates,
        "stock_code": ["sh.600487"] * len(dates),
        "open": [10.0 + i for i in range(len(dates))],
        "high": [10.5 + i for i in range(len(dates))],
        "low": [9.5 + i for i in range(len(dates))],
        "close": [10.2 + i for i in range(len(dates))],
        "volume": [1000 + i for i in range(len(dates))],
        "amount": [10_000.0 + i for i in range(len(dates))],
        "pct_change": [0.1 + i for i in range(len(dates))],
    })
    fetcher._save_cache("sh.600487", df)
    return fetcher


def test_get_stock_history_normalizes_supported_code_forms(tmp_path, monkeypatch):
    _cache_stock(tmp_path, monkeypatch)

    results = [
        json.loads(mcp_server.get_stock_history(code, start_date="2024-01-03", end_date="2024-01-05", limit=2))
        for code in ("600487", "sh.600487", "sh_600487")
    ]

    assert {result["stock_code"] for result in results} == {"sh.600487"}
    assert all(result["cache_status"] == "hit" for result in results)
    assert all(result["range_covered"] is True for result in results)
    assert all(result["returned_rows"] == 2 for result in results)
    assert results[0]["summary"]["latest_close"] == 13.2


def test_get_stock_history_missing_cache_returns_structured_error(tmp_path, monkeypatch):
    class TmpFetcher(MarketDataFetcher):
        def __init__(self):
            super().__init__(cache_dir=str(tmp_path))

    monkeypatch.setattr(mcp_server, "MarketDataFetcher", TmpFetcher)

    result = json.loads(mcp_server.get_stock_history("600487"))

    assert result["error_code"] == "STOCK_CACHE_MISSING"
    assert result["cache_status"] == "missing"
    assert result["stock_code"] == "sh.600487"
    assert result["cache_path"].endswith("stocks/sh_600487.parquet")


def test_get_stock_history_reports_partial_requested_coverage(tmp_path, monkeypatch):
    _cache_stock(tmp_path, monkeypatch, dates=["2024-01-03", "2024-01-04", "2024-01-05"])

    result = json.loads(mcp_server.get_stock_history("600487", start_date="2024-01-01", end_date="2024-01-05"))

    assert result["cache_status"] == "hit"
    assert result["cache_range"]["start_date"] == "2024-01-03"
    assert result["cache_range"]["end_date"] == "2024-01-05"
    assert result["range_covered"] is False
    assert result["returned_rows"] == 3
    assert result["rows"][0]["trade_date"] == "2024-01-03"


def test_check_market_cache_reports_universe_and_stock_cache(tmp_path, monkeypatch):
    _cache_stock(tmp_path, monkeypatch, dates=["2024-06-03", "2024-06-04"])
    universe_path = tmp_path / "universe" / "csi500_2024-06.txt"
    universe_path.parent.mkdir(parents=True)
    universe_path.write_text("sh.600487\nsz.000001\n")

    monkeypatch.setattr(mcp_server, "universe_cache_path", lambda universe, universe_date: universe_path)
    monkeypatch.setattr(mcp_server, "read_cached_universe", lambda universe, universe_date: ["sh.600487", "sz.000001"])
    monkeypatch.setattr(mcp_server, "list_universe_cache_months", lambda universe: ["2024-06"])

    result = json.loads(mcp_server.check_market_cache(
        universe="csi500",
        start_date="2024-06-03",
        end_date="2024-06-04",
        stock_code="600487",
    ))

    assert result["universe_cache_path"] == str(universe_path)
    assert result["universe_cache_exists"] is True
    assert result["universe_stock_count"] == 2
    assert result["stock_code"] == "sh.600487"
    assert result["stock_in_universe"] is True
    assert result["stock_cache"]["cache_status"] == "hit"
    assert result["stock_cache"]["range_covered"] is True
