"""Data snapshot metadata regressions."""

from datetime import datetime, timezone

import pandas as pd
import pytest

from quantgpt.data_snapshots import (
    attach_data_snapshot,
    build_cache_snapshot,
    build_fetch_snapshot,
    build_market_frame_snapshot,
    ensure_market_frame_snapshot,
    persist_data_snapshot,
    snapshot_result_fields,
)


def test_cache_snapshot_id_is_stable_and_changes_with_content(tmp_path):
    cache_file = tmp_path / "prices.parquet"
    cache_file.write_bytes(b"first")
    base_kwargs = {
        "query_params": {"universe": "hs300", "start": "2024-01-01"},
        "field_schema": ["trade_date", "stock_code", "close"],
        "row_count": 3,
        "date_min": "2024-01-01",
        "date_max": "2024-01-03",
    }

    first = build_cache_snapshot(cache_file, **base_kwargs)
    second = build_cache_snapshot(cache_file, **base_kwargs)
    cache_file.write_bytes(b"second")
    changed = build_cache_snapshot(cache_file, **base_kwargs)

    assert first["snapshot_id"].startswith("ds_")
    assert first["snapshot_id"] == second["snapshot_id"]
    assert first["content_hash"].startswith("sha256:")
    assert changed["snapshot_id"] != first["snapshot_id"]


def test_fetch_snapshot_id_excludes_download_time_but_keeps_metadata():
    first = build_fetch_snapshot(
        vendor="baostock",
        endpoint="query_history_k_data_plus",
        query_params={"code": "sh.600000", "start": "2024-01-01"},
        field_schema=["date", "code", "close"],
        row_count=2,
        date_min="2024-01-01",
        date_max="2024-01-02",
        download_time=datetime(2024, 1, 3, tzinfo=timezone.utc),
    )
    second = build_fetch_snapshot(
        vendor="baostock",
        endpoint="query_history_k_data_plus",
        query_params={"start": "2024-01-01", "code": "sh.600000"},
        field_schema=["date", "code", "close"],
        row_count=2,
        date_min="2024-01-01",
        date_max="2024-01-02",
        download_time=datetime(2024, 1, 4, tzinfo=timezone.utc),
    )

    assert first["snapshot_id"] == second["snapshot_id"]
    assert first["download_time"] != second["download_time"]
    assert first["query_params"]["endpoint"] == "query_history_k_data_plus"


def test_market_frame_snapshot_is_stable_and_attachable():
    frame = pd.DataFrame({
        "trade_date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
        "stock_code": ["A", "A"],
        "close": [1.0, 1.1],
        "volume": [100, 120],
    })
    snapshot = build_market_frame_snapshot(
        frame,
        vendor="unit",
        endpoint="test.market_frame",
        query_params={"universe": "small_scale"},
        source_metadata={"artifact": "ohlcv"},
    )
    attach_data_snapshot(frame, snapshot, source_metadata={"artifact": "ohlcv"})
    existing = ensure_market_frame_snapshot(frame, vendor="fallback")
    fields = snapshot_result_fields(existing, source_metadata=frame.attrs["source_metadata"])

    assert snapshot["snapshot_id"] == existing["snapshot_id"]
    assert snapshot["row_count"] == 2
    assert snapshot["date_min"] == "2024-01-02"
    assert frame.attrs["data_snapshot_id"] == snapshot["snapshot_id"]
    assert fields["data_snapshot_id"] == snapshot["snapshot_id"]
    assert fields["data_source_metadata"]["source_metadata"]["artifact"] == "ohlcv"


@pytest.mark.asyncio
async def test_persist_data_snapshot_is_idempotent(db_session, tmp_path):
    cache_file = tmp_path / "prices.parquet"
    cache_file.write_bytes(b"snapshot")
    snapshot = build_cache_snapshot(cache_file, row_count=1)

    first = await persist_data_snapshot(db_session, snapshot)
    second = await persist_data_snapshot(db_session, dict(snapshot))

    assert first.id == second.id
    assert first.snapshot_id == snapshot["snapshot_id"]
    assert first.source_kind == "cache_read_snapshot"
