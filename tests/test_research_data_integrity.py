"""P03 numerical and replay regressions; all inputs are synthetic."""

import pandas as pd
import pytest

from quantgpt.data_quality import DataQualityConfig, run_data_quality_gate
from quantgpt.data_snapshots import build_market_frame_snapshot, ensure_market_frame_snapshot


def panel():
    return pd.DataFrame([
        {"stock_code": code, "trade_date": date, "open": 10., "high": 10., "low": 10.,
         "close": 10., "volume": 100., "amount": 1000., "suspended": False}
        for date in pd.bdate_range("2024-01-02", periods=10) for code in ("A", "B")
    ])


def test_future_disappearance_does_not_delete_training_rows():
    frame = panel()
    cutoff = frame.trade_date.unique()[3]
    shortened = frame[~((frame.stock_code == "B") & (frame.trade_date > cutoff))]
    config = DataQualityConfig(adjustment="none")
    training, _ = run_data_quality_gate(frame[frame.trade_date <= cutoff], config)
    extended, _ = run_data_quality_gate(shortened, config)
    pd.testing.assert_frame_equal(training, extended[extended.trade_date <= cutoff])


@pytest.mark.parametrize("column,value", [("suspended", True), ("industry", "Technology"),
                                         ("total_share", 1000.), ("roe", .12),
                                         ("revision_id", "amended")])
def test_every_input_field_participates_in_snapshot_identity(column, value):
    frame = panel().assign(**{column: None})
    before = build_market_frame_snapshot(frame)
    frame.loc[0, column] = value
    assert build_market_frame_snapshot(frame)["snapshot_id"] != before["snapshot_id"]


def test_enrichment_invalidates_inherited_frame_snapshot():
    frame = panel()
    before = ensure_market_frame_snapshot(frame)
    enriched = frame.assign(roe=.1)
    assert ensure_market_frame_snapshot(enriched)["snapshot_id"] != before["snapshot_id"]


def test_snapshot_identity_is_independent_of_row_and_column_order():
    frame = panel()
    permuted = frame.sample(frac=1, random_state=7).iloc[:, ::-1]
    assert build_market_frame_snapshot(frame)["snapshot_id"] == build_market_frame_snapshot(permuted)["snapshot_id"]


def test_snapshot_replay_is_immutable_and_detects_corruption(tmp_path):
    from quantgpt.data_snapshots import freeze_market_frame, load_frozen_market_frame

    frame = panel().assign(available_at=pd.Timestamp("2024-01-01", tz="UTC"), industry="Tech")
    snapshot = freeze_market_frame(frame, tmp_path, vendor="synthetic")
    frame.loc[0, "close"] = 999.
    restored = load_frozen_market_frame(snapshot["snapshot_id"], tmp_path)
    assert restored.loc[0, "close"] == 10.
    assert restored.attrs["data_snapshot"]["replayable"] is True
    path = tmp_path / snapshot["snapshot_id"] / "market.parquet"
    path.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="content hash"):
        load_frozen_market_frame(snapshot["snapshot_id"], tmp_path)


def test_vintages_use_availability_and_keep_prior_version():
    from quantgpt.pit_data import align_vintages

    facts = pd.DataFrame([
        {"security_id": "A", "field": "eps", "period_end": "2023-12-31", "value": 2.,
         "available_at": "2024-02-01T21:10:00Z", "revision_id": "original", "unit": "USD/share"},
        {"security_id": "A", "field": "eps", "period_end": "2023-12-31", "value": 3.,
         "available_at": "2024-03-01T21:10:00Z", "revision_id": "revised", "unit": "USD/share"},
    ])
    decisions = pd.DataFrame({"security_id": ["A"] * 3,
                              "decision_at": ["2024-02-01T14:30:00Z", "2024-02-02T14:30:00Z",
                                              "2024-03-02T14:30:00Z"]})
    output = align_vintages(decisions, facts, fields=["eps"])
    assert pd.isna(output.eps.iloc[0])
    assert output.eps.iloc[1:].tolist() == [2., 3.]
    assert output.eps_revision_id.iloc[1:].tolist() == ["original", "revised"]


def test_financial_provider_never_maps_shares_or_ratios_to_economic_proxies():
    from quantgpt.fundamental_data import _RQ_FACTOR_MAP

    for field in ("total_share", "float_share", "yoy_equity", "yoy_asset", "cfo_to_np"):
        assert field not in _RQ_FACTOR_MAP
