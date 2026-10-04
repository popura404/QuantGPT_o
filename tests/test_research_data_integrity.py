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


def test_calendar_and_actions_are_hashed_and_restored_and_manifest_is_verified(tmp_path):
    import json

    from quantgpt.data_snapshots import freeze_market_frame, load_frozen_market_frame

    frame = panel()
    frame.attrs.update(calendar_sessions=list(pd.date_range("2024-01-02", periods=10)),
                       corporate_actions=[{"stock_code": "A", "split_ratio": 2., "session": "2024-01-08"}])
    before = ensure_market_frame_snapshot(frame)
    frame.attrs["corporate_actions"][0]["split_ratio"] = 3.
    after = ensure_market_frame_snapshot(frame)
    assert before["snapshot_id"] != after["snapshot_id"]
    frozen = freeze_market_frame(frame, tmp_path, vendor="synthetic")
    restored = load_frozen_market_frame(frozen["snapshot_id"], tmp_path)
    assert restored.attrs["corporate_actions"][0]["split_ratio"] == 3.
    assert len(restored.attrs["calendar_sessions"]) == 10
    manifest = tmp_path / frozen["snapshot_id"] / "manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["source_metadata"]["input_attributes"]["corporate_actions"][0]["split_ratio"] = 99.
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="identity"):
        load_frozen_market_frame(frozen["snapshot_id"], tmp_path)


def test_supplied_calendar_detects_whole_panel_missing_sessions():
    from quantgpt.data_quality import historical_data_eligibility

    frame = panel().iloc[[0, 4]].copy()
    frame.attrs["calendar_sessions"] = pd.date_range("2024-01-02", periods=3)
    assert historical_data_eligibility(frame).tolist() == [True, False]


@pytest.mark.parametrize("name", ["pb", "ps", "roa", "bps", "nav"])
def test_quarterly_ratios_do_not_substitute_for_missing_economic_denominators(name):
    from quantgpt.fundamental_data import FundamentalDataFetcher
    from quantgpt.pit_data import DataCapabilityError

    with pytest.raises(DataCapabilityError, match="exact_balance_or_ttm"):
        FundamentalDataFetcher().align_to_daily(pd.DataFrame(), panel(), {name})


def test_quarterly_pe_uses_ttm_eps_and_rejects_adjusted_price_basis():
    from quantgpt.fundamental_data import FundamentalDataFetcher
    from quantgpt.pit_data import DataCapabilityError

    facts = pd.DataFrame({"stock_code": ["A"], "stat_date": ["2023-12-31"], "pub_date": ["2024-01-01"],
                          "eps_ttm": [2.], "net_profit": [9999.], "total_share": [1.]})
    market = panel()
    market.attrs["adjustment"] = "raw"
    result = FundamentalDataFetcher().align_to_daily(facts, market, {"pe"})
    assert result.loc[result.stock_code == "A", "pe"].eq(5.).all()
    market.attrs["adjustment"] = "qfq"
    with pytest.raises(DataCapabilityError, match="share_basis"):
        FundamentalDataFetcher().align_to_daily(facts, market, {"pe"})


def test_late_old_period_revision_cannot_replace_newer_available_period():
    from quantgpt.fundamental_data import FundamentalDataFetcher

    facts = pd.DataFrame([
        {"stock_code": "A", "stat_date": "2023-09-30", "pub_date": "2023-11-01", "roe": .10},
        {"stock_code": "A", "stat_date": "2023-12-31", "pub_date": "2024-01-02", "roe": .20},
        {"stock_code": "A", "stat_date": "2023-09-30", "pub_date": "2024-01-03", "roe": .99},
    ])
    result = FundamentalDataFetcher().align_to_daily(facts, panel(), {"roe"})
    assert result.loc[(result.stock_code == "A") & (result.trade_date >= "2024-01-04"), "roe"].eq(.20).all()
