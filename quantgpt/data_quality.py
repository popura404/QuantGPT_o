"""Base market-data quality gate for factor backtests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class DataQualityConfig:
    enabled: bool = True
    mode: Literal["report_only", "filter", "strict"] = "filter"

    min_price: float = 0.01
    max_abs_daily_ret: float = 0.25
    max_missing_ratio_per_stock: float = 0.2

    require_positive_volume: bool = True
    require_positive_amount: bool = True

    drop_st: bool = False
    drop_new_listing_days: int = 60

    adjustment: Literal["qfq", "hfq", "none", "unknown"] = "unknown"
    fail_on_unknown_adjustment: bool = False


_REQUIRED_COLUMNS = {"trade_date", "stock_code", "open", "high", "low", "close", "volume", "amount"}


def _issue(rule: str, **payload) -> dict:
    out = {"rule": rule}
    out.update(payload)
    return out


def run_data_quality_gate(
    market_df: pd.DataFrame,
    config: DataQualityConfig | None = None,
) -> tuple[pd.DataFrame, dict]:
    """Validate and optionally filter base OHLCV market data."""
    config = config or DataQualityConfig()
    if config.mode not in {"report_only", "filter", "strict"}:
        raise ValueError("data quality mode must be report_only, filter, or strict")

    missing = sorted(_REQUIRED_COLUMNS - set(market_df.columns))
    if missing:
        raise ValueError(f"market_df missing required columns: {missing}")

    before_rows = int(len(market_df))
    before_stocks = int(market_df["stock_code"].nunique())
    report = {
        "enabled": bool(config.enabled),
        "before_rows": before_rows,
        "after_rows": before_rows,
        "dropped_rows": 0,
        "before_stocks": before_stocks,
        "after_stocks": before_stocks,
        "dropped_stocks": 0,
        "adjustment": config.adjustment,
        "data_quality_scope": "full_requested_sample",
        "issues": [],
        "warnings": [],
    }

    if not config.enabled:
        return market_df.copy(), report

    if config.adjustment == "unknown":
        warning = "price adjustment mode is unknown; qfq/hfq/none should be explicit for reproducible backtests"
        if config.fail_on_unknown_adjustment:
            raise ValueError(warning)
        report["warnings"].append(warning)
    if config.drop_st and "is_st" not in market_df.columns:
        report["warnings"].append("ST metadata is unavailable; drop_st was not applied")
    if config.drop_new_listing_days and "listing_date" not in market_df.columns:
        report["warnings"].append("listing-date metadata is unavailable; drop_new_listing_days was not applied")

    df = market_df.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.sort_values(["stock_code", "trade_date"])

    invalid_masks: list[pd.Series] = []

    price_cols = ["open", "high", "low", "close"]
    bad_price = df[price_cols].isna().any(axis=1) | (df[price_cols] <= config.min_price).any(axis=1)
    if int(bad_price.sum()) > 0:
        report["issues"].append(_issue("bad_price", rows=int(bad_price.sum())))
        invalid_masks.append(bad_price)

    bad_ohlc = (
        (df["high"] < df[["open", "close", "low"]].max(axis=1))
        | (df["low"] > df[["open", "close", "high"]].min(axis=1))
    )
    if int(bad_ohlc.sum()) > 0:
        report["issues"].append(_issue("bad_ohlc", rows=int(bad_ohlc.sum())))
        invalid_masks.append(bad_ohlc)

    if config.require_positive_volume:
        bad_volume = df["volume"].isna() | (df["volume"] <= 0)
        if int(bad_volume.sum()) > 0:
            report["issues"].append(_issue("bad_volume", rows=int(bad_volume.sum())))
            invalid_masks.append(bad_volume)

    if config.require_positive_amount:
        bad_amount = df["amount"].isna() | (df["amount"] <= 0)
        if int(bad_amount.sum()) > 0:
            report["issues"].append(_issue("bad_amount", rows=int(bad_amount.sum())))
            invalid_masks.append(bad_amount)

    close_for_returns = df["close"].where(~bad_price)
    decimal_ret = close_for_returns.groupby(df["stock_code"]).pct_change()
    bad_extreme = decimal_ret.abs() > config.max_abs_daily_ret
    bad_extreme = bad_extreme.fillna(False)
    if int(bad_extreme.sum()) > 0:
        report["issues"].append(
            _issue("extreme_return", rows=int(bad_extreme.sum()), max_abs_daily_ret=config.max_abs_daily_ret)
        )
        invalid_masks.append(bad_extreme)

    expected_days = max(1, int(df["trade_date"].nunique()))
    observed_days = df.groupby("stock_code")["trade_date"].nunique()
    missing_ratio = 1 - observed_days / expected_days
    high_missing_stocks = sorted(missing_ratio[missing_ratio > config.max_missing_ratio_per_stock].index.tolist())
    if high_missing_stocks:
        report["issues"].append(
            _issue(
                "high_missing_ratio_stock",
                stocks=len(high_missing_stocks),
                max_missing_ratio=config.max_missing_ratio_per_stock,
            )
        )

    row_invalid = pd.Series(False, index=df.index)
    for mask in invalid_masks:
        row_invalid |= mask.reindex(df.index, fill_value=False)

    has_filterable_issues = bool(row_invalid.any() or high_missing_stocks)
    if config.mode == "strict" and has_filterable_issues:
        raise ValueError(f"data quality violations detected: {report['issues']}")

    if config.mode == "report_only":
        cleaned = df.copy()
    else:
        cleaned = df[~row_invalid].copy()
        if high_missing_stocks:
            cleaned = cleaned[~cleaned["stock_code"].isin(high_missing_stocks)].copy()

    report["after_rows"] = int(len(cleaned))
    report["dropped_rows"] = int(before_rows - len(cleaned))
    report["after_stocks"] = int(cleaned["stock_code"].nunique())
    report["dropped_stocks"] = int(before_stocks - cleaned["stock_code"].nunique())

    # Avoid leaking numpy scalar types into API payloads.
    for issue in report["issues"]:
        for key, value in list(issue.items()):
            if isinstance(value, np.generic):
                issue[key] = value.item()
    return cleaned, report
