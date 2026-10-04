"""Offline cache publication, calendar coverage and adjustment basis regressions."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import pytest

from quantgpt import market_data
from quantgpt.market_data import MarketDataFetcher, _atomic_market_cache, describe_stock_cache


def sample(dates, close=10., adjustment="qfq", version="unknown"):
    frame = pd.DataFrame({"trade_date": pd.to_datetime(dates), "stock_code": "sh.600000",
                          "open": close, "high": close, "low": close, "close": close,
                          "volume": 100., "amount": 1000., "pct_change": 0.})
    frame.attrs["cache_basis"] = {"provider": "test", "feed": "fixture", "adjustment": adjustment, "version": version}
    return frame


def test_failed_atomic_write_preserves_previous_file_and_removes_temporary(tmp_path, monkeypatch):
    path = tmp_path / "market.parquet"
    path.write_bytes(b"previous-cache")

    def fail(frame, target, **kwargs):
        Path(target).write_bytes(b"incomplete")
        raise OSError("simulated disk failure")

    monkeypatch.setattr(pd.DataFrame, "to_parquet", fail)
    with pytest.raises(OSError, match="simulated"):
        _atomic_market_cache(sample(["2024-01-02"]), path)
    assert path.read_bytes() == b"previous-cache"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["market.parquet"]


def test_internal_missing_session_is_not_hidden_by_matching_endpoints(tmp_path):
    fetcher = MarketDataFetcher(str(tmp_path))
    fetcher._save_cache("sh.600000", sample(["2024-01-02", "2024-01-04"]))
    report = describe_stock_cache("sh.600000", "2024-01-02", "2024-01-04", cache_dir=str(tmp_path))
    assert report["range_covered"] is False
    assert report["missing_sessions"] == ["2024-01-03"]
    assert fetcher.fetch_stocks(["sh.600000"], "2024-01-02", "2024-01-04", cache_only=True) is None


def test_china_holiday_and_weekend_do_not_require_fake_bars():
    frame = sample(["2024-02-08", "2024-02-19"])
    assert market_data.missing_market_sessions(frame, "2024-02-08", "2024-02-19").empty


def test_adjusted_refresh_replaces_whole_basis_and_rejects_partial_history(tmp_path):
    fetcher = MarketDataFetcher(str(tmp_path))
    original = sample(["2024-01-02", "2024-01-03"], close=100.)
    fetcher._save_cache("sh.600000", original)
    assert fetcher._refresh_bounds("sh.600000", "2024-01-04", "2024-01-04") == ("2024-01-02", "2024-01-04")
    partial = sample(["2024-01-04"], close=50.)
    with pytest.raises(ValueError, match="complete replacement"):
        fetcher._combine_refresh(original, partial)
    replacement = sample(["2024-01-02", "2024-01-03", "2024-01-04"], close=50.)
    assert fetcher._combine_refresh(original, replacement)["close"].tolist() == [50., 50., 50.]
    assert fetcher._load_cache("sh.600000")["close"].tolist() == [100., 100.]


def test_verified_raw_basis_can_fetch_only_missing_sessions(tmp_path):
    fetcher = MarketDataFetcher(str(tmp_path))
    old = sample(["2024-01-02", "2024-01-04"], adjustment="raw", version="v1")
    fetcher._save_cache("sh.600000", old)
    assert fetcher._refresh_bounds("sh.600000", "2024-01-02", "2024-01-04") == ("2024-01-03", "2024-01-03")
    incoming = sample(["2024-01-03"], adjustment="raw", version="v1")
    merged = fetcher._combine_refresh(old, incoming)
    assert len(merged) == 3
    assert merged.attrs == old.attrs
    incoming.attrs["cache_basis"]["version"] = "v2"
    with pytest.raises(ValueError, match="complete replacement"):
        fetcher._combine_refresh(old, incoming)


def test_concurrent_fetchers_publish_once_and_second_reader_uses_cache(tmp_path, monkeypatch):
    calls = []

    def remote(self, code, start, end, already_logged_in=False):
        calls.append((code, start, end))
        return sample(["2024-01-02"])

    monkeypatch.setattr(market_data, "HAS_BAOSTOCK", True)
    monkeypatch.setattr(market_data, "_baostock_login", lambda: True)
    monkeypatch.setattr(market_data, "_baostock_logout", lambda: None)
    monkeypatch.setattr(MarketDataFetcher, "_fetch_remote_bs", remote)
    fetchers = [MarketDataFetcher(str(tmp_path)) for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(f.fetch_stocks, ["sh.600000"], "2024-01-02", "2024-01-02", False) for f in fetchers]
        results = [future.result(timeout=10) for future in futures]
    assert len(calls) == 1
    assert all(result is not None and len(result) == 1 for result in results)
