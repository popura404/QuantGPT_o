"""Data-quality gate regressions."""

import pandas as pd
import pytest

from quantgpt.data_quality import DataQualityConfig, run_data_quality_gate


def _base_market_df(days: int = 8, stocks: tuple[str, ...] = ("A", "B", "C")) -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-02", periods=days)
    rows = []
    for s_idx, stock in enumerate(stocks):
        price = 10.0 + s_idx
        for date in dates:
            close = price * 1.01
            rows.append({
                "trade_date": date,
                "stock_code": stock,
                "open": price,
                "high": max(price, close) * 1.01,
                "low": min(price, close) * 0.99,
                "close": close,
                "volume": 1_000,
                "amount": 1_000 * close,
                "pct_change": 1.0,
            })
            price = close
    return pd.DataFrame(rows)


def test_missing_required_columns_raise():
    df = _base_market_df().drop(columns=["amount"])

    with pytest.raises(ValueError, match="missing required columns"):
        run_data_quality_gate(df)


@pytest.mark.parametrize(
    "mutate,rule",
    [
        (lambda df: df.assign(close=[0.0] + df["close"].tolist()[1:]), "bad_price"),
        (lambda df: df.assign(high=[1.0] + df["high"].tolist()[1:]), "bad_ohlc"),
        (lambda df: df.assign(volume=[0] + df["volume"].tolist()[1:]), "bad_volume"),
        (lambda df: df.assign(amount=[0] + df["amount"].tolist()[1:]), "bad_amount"),
    ],
)
def test_row_level_issues_are_filtered(mutate, rule):
    df = mutate(_base_market_df())

    cleaned, report = run_data_quality_gate(df, DataQualityConfig(adjustment="qfq"))

    assert len(cleaned) == len(df) - 1
    assert any(issue["rule"] == rule for issue in report["issues"])


def test_extreme_return_uses_decimal_close_return_not_pct_change_points():
    df = _base_market_df(days=4, stocks=("A",))
    df.loc[df.index[1], "close"] = df.loc[df.index[0], "close"] * 1.5
    df["pct_change"] = 5.0

    cleaned, report = run_data_quality_gate(
        df,
        DataQualityConfig(adjustment="qfq", max_abs_daily_ret=0.25),
    )

    assert len(cleaned) < len(df)
    assert any(issue["rule"] == "extreme_return" for issue in report["issues"])


def test_st_and_suspended_rows_are_filtered_when_metadata_available():
    df = _base_market_df(days=4, stocks=("A",))
    df["is_st"] = 0
    df["trade_status"] = 1
    df.loc[df.index[0], "is_st"] = 1
    df.loc[df.index[1], "trade_status"] = 0

    cleaned, report = run_data_quality_gate(df, DataQualityConfig(adjustment="qfq"))

    assert len(cleaned) == len(df) - 2
    assert any(issue["rule"] == "st_stock" for issue in report["issues"])
    assert any(issue["rule"] == "suspended" for issue in report["issues"])


def test_one_price_limit_rows_are_filtered_with_pre_close():
    df = _base_market_df(days=4, stocks=("A",))
    df.loc[df.index[1], ["open", "high", "low", "close"]] = 11.0
    df["pre_close"] = df.groupby("stock_code")["close"].shift(1)
    df.loc[df.index[1], "pre_close"] = 10.0
    df.loc[df.index[1], "pct_change"] = 10.0

    cleaned, report = run_data_quality_gate(df, DataQualityConfig(adjustment="qfq"))

    assert len(cleaned) == len(df) - 1
    assert any(issue["rule"] == "one_price_limit" for issue in report["issues"])


def test_return_adjustment_mismatch_is_reported_but_not_filtered():
    df = _base_market_df(days=4, stocks=("A",))
    df["pre_close"] = df.groupby("stock_code")["close"].shift(1)
    df.loc[df.index[1], "pre_close"] = df.loc[df.index[0], "close"]
    df.loc[df.index[1], "pct_change"] = -5.0

    cleaned, report = run_data_quality_gate(df, DataQualityConfig(adjustment="qfq"))

    assert len(cleaned) == len(df)
    assert any(issue["rule"] == "return_adjustment_mismatch" for issue in report["issues"])
    assert any("pct_change is inconsistent" in warning for warning in report["warnings"])


def test_listing_age_window_is_filtered_when_listing_date_available():
    df = _base_market_df(days=4, stocks=("A",))
    df["listing_date"] = pd.Timestamp("2024-01-01")

    cleaned, report = run_data_quality_gate(
        df,
        DataQualityConfig(adjustment="qfq", drop_new_listing_days=5),
    )

    assert cleaned.empty
    assert any(issue["rule"] == "new_listing_window" for issue in report["issues"])


def test_high_missing_ratio_stock_is_filtered():
    df = _base_market_df(days=10, stocks=("A", "B"))
    df = df[~((df["stock_code"] == "B") & (df["trade_date"] > df["trade_date"].min()))].copy()

    cleaned, report = run_data_quality_gate(
        df,
        DataQualityConfig(adjustment="qfq", max_missing_ratio_per_stock=0.2),
    )

    assert set(cleaned["stock_code"].unique()) == {"A"}
    assert report["data_quality_scope"] == "full_requested_sample"
    assert any(issue["rule"] == "high_missing_ratio_stock" for issue in report["issues"])


def test_report_only_keeps_rows_and_unknown_adjustment_warns():
    df = _base_market_df()
    df.loc[df.index[0], "volume"] = 0

    cleaned, report = run_data_quality_gate(df, DataQualityConfig(mode="report_only"))

    assert len(cleaned) == len(df)
    assert report["warnings"]
    assert any(issue["rule"] == "bad_volume" for issue in report["issues"])


def test_fail_on_unknown_adjustment_raises():
    with pytest.raises(ValueError, match="price adjustment mode is unknown"):
        run_data_quality_gate(_base_market_df(), DataQualityConfig(fail_on_unknown_adjustment=True))


def test_strict_mode_raises_on_filterable_violations():
    df = _base_market_df()
    df.loc[df.index[0], "amount"] = 0

    with pytest.raises(ValueError, match="data quality violations"):
        run_data_quality_gate(df, DataQualityConfig(mode="strict", adjustment="qfq"))


def test_fundamental_missingness_is_outside_cycle1_filter_scope():
    df = _base_market_df()
    df["pe_ttm"] = None

    cleaned, report = run_data_quality_gate(df, DataQualityConfig(adjustment="qfq"))

    assert len(cleaned) == len(df)
    assert report["data_quality_scope"] == "full_requested_sample"
    assert not any(issue["rule"] == "fundamental_missing" for issue in report["issues"])
