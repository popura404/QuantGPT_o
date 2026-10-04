"""Base market-data quality gate for factor backtests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

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

    drop_st: bool = True
    drop_suspended: bool = True
    drop_one_price_limit: bool = True
    drop_new_listing_days: int = 60

    adjustment: Literal["qfq", "hfq", "none", "unknown"] = "unknown"
    fail_on_unknown_adjustment: bool = False
    return_mismatch_tolerance_pct_points: float = 0.2
    price_limit_tolerance: float = 0.001


_REQUIRED_COLUMNS = {"trade_date", "stock_code", "open", "high", "low", "close", "volume", "amount"}


def _issue(rule: str, **payload) -> dict:
    out = {"rule": rule}
    out.update(payload)
    return out


def _truthy_mask(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.fillna(False)
    if pd.api.types.is_numeric_dtype(series):
        return cast(pd.Series, pd.to_numeric(series, errors="coerce")).fillna(0) != 0
    normalized = series.astype(str).str.strip().str.lower()
    return normalized.isin({"1", "true", "t", "yes", "y", "是", "st", "*st"})


def _trade_status_suspended_mask(series: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(series):
        return cast(pd.Series, pd.to_numeric(series, errors="coerce")).fillna(1) != 1
    normalized = series.astype(str).str.strip().str.lower()
    active_values = {"1", "true", "t", "yes", "y", "交易", "trading", "active"}
    missing_values = {"", "none", "nan", "nat"}
    return ~(normalized.isin(active_values) | normalized.isin(missing_values))


def _all_ohlc_equal(df: pd.DataFrame, tolerance: float) -> pd.Series:
    max_price = df[["open", "high", "low", "close"]].max(axis=1)
    min_price = df[["open", "high", "low", "close"]].min(axis=1)
    denom = df["close"].abs().clip(lower=1.0)
    return ((max_price - min_price).abs() / denom) <= tolerance


def _near_price(left: pd.Series, right: pd.Series, tolerance: float) -> pd.Series:
    denom = right.abs().clip(lower=1.0)
    return ((left - right).abs() / denom) <= tolerance


def _one_price_limit_mask(df: pd.DataFrame, tolerance: float) -> tuple[pd.Series, str]:
    one_price = _all_ohlc_equal(df, tolerance)
    if "limit_up" in df.columns or "limit_down" in df.columns:
        limit_up = cast(pd.Series, pd.to_numeric(df["limit_up"], errors="coerce")) if "limit_up" in df.columns else pd.Series(np.nan, index=df.index)
        limit_down = cast(pd.Series, pd.to_numeric(df["limit_down"], errors="coerce")) if "limit_down" in df.columns else pd.Series(np.nan, index=df.index)
        at_up = limit_up.notna() & _near_price(cast(pd.Series, df["close"]), limit_up, tolerance)
        at_down = limit_down.notna() & _near_price(cast(pd.Series, df["close"]), limit_down, tolerance)
        return one_price & (at_up | at_down), "limit_price"

    if "pre_close" in df.columns:
        pre_close = cast(pd.Series, pd.to_numeric(df["pre_close"], errors="coerce"))
        implied_ret = df["close"] / pre_close - 1
        rough_limit = implied_ret.abs() >= 0.049
        rough_limit &= pre_close.notna() & (pre_close > 0)
        return one_price & rough_limit, "pre_close_rough"

    if "pct_change" in df.columns:
        pct_change = cast(pd.Series, pd.to_numeric(df["pct_change"], errors="coerce"))
        rough_limit = pct_change.abs() >= 4.9
        return one_price & rough_limit, "pct_change_rough"

    return pd.Series(False, index=df.index), "unavailable"


def _return_mismatch_mask(df: pd.DataFrame, tolerance_pct_points: float) -> pd.Series:
    if "pre_close" not in df.columns or "pct_change" not in df.columns:
        return pd.Series(False, index=df.index)
    pre_close = cast(pd.Series, pd.to_numeric(df["pre_close"], errors="coerce"))
    pct_change = cast(pd.Series, pd.to_numeric(df["pct_change"], errors="coerce"))
    implied_pct = (df["close"] / pre_close - 1) * 100
    valid = pre_close.notna() & (pre_close > 0) & pct_change.notna() & implied_pct.notna()
    return valid & ((implied_pct - pct_change).abs() > tolerance_pct_points)


def run_data_quality_gate(
    market_df: pd.DataFrame,
    config: DataQualityConfig | None = None,
) -> tuple[pd.DataFrame, dict]:
    """Validate and optionally filter base OHLCV market data."""
    config = config or DataQualityConfig()
    if not market_df.columns.is_unique:
        raise ValueError("market_df columns must be unique")
    if config.mode not in {"report_only", "filter", "strict"}:
        raise ValueError("data quality mode must be report_only, filter, or strict")

    missing = sorted(_REQUIRED_COLUMNS - set(market_df.columns))
    if missing:
        raise ValueError(f"market_df missing required columns: {missing}")

    before_rows = int(len(market_df))
    before_stocks = int(cast(pd.Series, market_df["stock_code"]).nunique())
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
        "eligibility_scope": "decision_time_history",
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
    if config.drop_suspended and "trade_status" not in market_df.columns and "suspended" not in market_df.columns:
        report["warnings"].append("trade-status metadata is unavailable; suspension filtering used OHLCV only")

    df = market_df.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.sort_values(["stock_code", "trade_date"])
    if "listing_date" in df.columns:
        df["listing_date"] = pd.to_datetime(df["listing_date"], errors="coerce")

    invalid_masks: list[pd.Series] = []
    strict_issue_rules: list[str] = []

    if config.drop_st and "is_st" in df.columns:
        st_mask = _truthy_mask(cast(pd.Series, df["is_st"])).reindex(df.index, fill_value=False)
        if int(st_mask.sum()) > 0:
            report["issues"].append(_issue("st_stock", rows=int(st_mask.sum())))
            invalid_masks.append(st_mask)

    if config.drop_suspended:
        suspended_masks = []
        if "trade_status" in df.columns:
            suspended_masks.append(_trade_status_suspended_mask(cast(pd.Series, df["trade_status"])))
        if "suspended" in df.columns:
            suspended_masks.append(_truthy_mask(cast(pd.Series, df["suspended"])))
        if suspended_masks:
            suspended = pd.Series(False, index=df.index)
            for mask in suspended_masks:
                suspended |= mask.reindex(df.index, fill_value=False)
            if int(suspended.sum()) > 0:
                report["issues"].append(_issue("suspended", rows=int(suspended.sum())))
                invalid_masks.append(suspended)

    if config.drop_new_listing_days and "listing_date" in df.columns:
        age_days = (df["trade_date"] - df["listing_date"]).dt.days
        new_listing = df["listing_date"].notna() & (age_days >= 0) & (age_days < config.drop_new_listing_days)
        if int(new_listing.sum()) > 0:
            report["issues"].append(
                _issue("new_listing_window", rows=int(new_listing.sum()), min_listing_age_days=config.drop_new_listing_days)
            )
            invalid_masks.append(new_listing)

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
    decimal_ret = close_for_returns.groupby(df["stock_code"]).pct_change(fill_method=None)
    bad_extreme = decimal_ret.abs() > config.max_abs_daily_ret
    bad_extreme = bad_extreme.fillna(False)
    if int(bad_extreme.sum()) > 0:
        report["issues"].append(
            _issue("extreme_return", rows=int(bad_extreme.sum()), max_abs_daily_ret=config.max_abs_daily_ret)
        )
        invalid_masks.append(bad_extreme)

    if config.drop_one_price_limit:
        one_price_limit, limit_source = _one_price_limit_mask(df, config.price_limit_tolerance)
        if int(one_price_limit.sum()) > 0:
            report["issues"].append(_issue("one_price_limit", rows=int(one_price_limit.sum()), source=limit_source))
            invalid_masks.append(one_price_limit)

    return_mismatch = _return_mismatch_mask(df, config.return_mismatch_tolerance_pct_points)
    if int(return_mismatch.sum()) > 0:
        report["issues"].append(
            _issue(
                "return_adjustment_mismatch",
                rows=int(return_mismatch.sum()),
                tolerance_pct_points=config.return_mismatch_tolerance_pct_points,
            )
        )
        report["warnings"].append(
            "pct_change is inconsistent with close/pre_close; check adjustment mode or mixed adjusted/unadjusted data"
        )
        strict_issue_rules.append("return_adjustment_mismatch")

    expected_days = max(1, int(cast(pd.Series, df["trade_date"]).nunique()))
    observed_days = cast(pd.Series, df.groupby("stock_code")["trade_date"].nunique())
    missing_ratio = cast(pd.Series, 1 - observed_days / expected_days)
    high_missing_stocks = sorted(cast(pd.Series, missing_ratio[missing_ratio > config.max_missing_ratio_per_stock]).index.tolist())
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
    if config.mode == "strict" and (has_filterable_issues or strict_issue_rules):
        raise ValueError(f"data quality violations detected: {report['issues']}")

    if config.mode == "report_only":
        cleaned = df.copy()
    else:
        cleaned = cast(pd.DataFrame, df[~row_invalid]).copy()
        # The full-sample report is diagnostic, never a historical universe filter.
        # Only the prefix available at each decision can determine eligibility.
        eligibility = historical_data_eligibility(df, config.max_missing_ratio_per_stock)
        cleaned = cast(pd.DataFrame, cleaned[eligibility.reindex(cleaned.index, fill_value=False)]).copy()

    report["after_rows"] = int(len(cleaned))
    report["dropped_rows"] = int(before_rows - len(cleaned))
    report["after_stocks"] = int(cast(pd.Series, cleaned["stock_code"]).nunique())
    report["dropped_stocks"] = before_stocks - report["after_stocks"]

    # Avoid leaking numpy scalar types into API payloads.
    for issue in report["issues"]:
        for key, value in list(issue.items()):
            if isinstance(value, np.generic):
                issue[key] = value.item()
    return cleaned, report


def historical_data_eligibility(frame: pd.DataFrame, max_missing_ratio: float = 0.2) -> pd.Series:
    """Use only observed session prefixes; future delisting cannot erase past rows.

    Counting starts at the first observed session, not at an inferred listing date.
    This is a data-availability filter, not a claim of historical universe coverage.
    A supplied provider calendar also detects sessions missing from the entire panel.
    """
    if not 0 <= max_missing_ratio <= 1:
        raise ValueError("max_missing_ratio must be between 0 and 1")
    if frame.empty:
        return pd.Series(True, index=frame.index, dtype=bool)
    dates = pd.to_datetime(frame["trade_date"])
    supplied = frame.attrs.get("calendar_sessions")
    sessions = pd.DatetimeIndex(pd.to_datetime(supplied)).sort_values().unique() if supplied is not None else pd.DatetimeIndex(dates.unique()).sort_values()
    if not pd.Index(dates).isin(sessions).all():
        raise ValueError("observed dates are outside the supplied trading calendar")
    positions = pd.Series(sessions.get_indexer(dates), index=frame.index)
    first = positions.groupby(frame["stock_code"]).transform("min")
    ordered = frame.assign(_session_position=positions).sort_values(["stock_code", "trade_date"])
    if ordered.duplicated(["stock_code", "trade_date"]).any():
        raise ValueError("duplicate security/session rows")
    observed = (ordered.groupby("stock_code").cumcount() + 1).reindex(frame.index)
    missing_ratio = 1 - observed / (positions - first + 1)
    return (missing_ratio <= max_missing_ratio).astype(bool)
