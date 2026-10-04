"""Factor neutralization — QuantGPT
Copyright (c) 2026 Miasyster. Licensed under the MIT License.
https://github.com/Miasyster/QuantGPT

Removes systematic exposures (industry, market-cap) from factor values
before backtesting, so that the factor captures alpha rather than beta/style tilts.
"""

import logging
import threading
import time
from pathlib import Path
from typing import cast

import numpy as np
import pandas as pd

from .expression_parser import FieldCapabilityError

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Global lock for baostock
_bs_lock = threading.Lock()


def _session_column(df: pd.DataFrame) -> str:
    name = "session" if "session" in df else "trade_date"
    if name not in df or df[name].isna().to_numpy().any():
        raise FieldCapabilityError("session", "a non-missing session is required for cross-sectional neutralization")
    return name


def _require_classification(df: pd.DataFrame, column: str) -> None:
    if column not in df:
        raise FieldCapabilityError(column, "supply point-in-time classifications for every input row")
    labels = cast(pd.Series, df[column])
    if labels.isna().any() or labels.astype(str).str.strip().eq("").any():
        raise FieldCapabilityError(column, "supply point-in-time classifications for every input row")


def industry_neutralize(
    factor_df: pd.DataFrame,
    industry_col: str = "industry",
) -> pd.Series:
    """Industry-neutralize factor values (cross-sectional within each date).

    Subtracts industry mean from factor values → within-industry relative ranking.

    Args:
        factor_df: DataFrame with columns [trade_date, stock_code, factor_value, industry].
        industry_col: Column name for industry classification.

    Returns:
        Neutralized factor values as Series.
    """
    _require_classification(factor_df, industry_col)
    session = _session_column(factor_df)
    fv = factor_df["factor_value"].replace([np.inf, -np.inf], np.nan)
    # transform retains the original row identity, including stock-sorted input.
    mean = fv.groupby([factor_df[session], factor_df[industry_col]], sort=False).transform("mean")
    return fv - mean


def cap_neutralize(
    factor_df: pd.DataFrame,
    cap_col: str = "market_cap",
) -> pd.Series:
    """Market-cap neutralize factor values (cross-sectional regression residual).

    Regresses factor values on log(market_cap) per date, returns residuals.

    Args:
        factor_df: DataFrame with columns [trade_date, stock_code, factor_value, market_cap].
        cap_col: Column name for market capitalization.

    Returns:
        Neutralized factor values as Series (regression residuals).
    """
    session = _session_column(factor_df)
    if cap_col not in factor_df:
        raise FieldCapabilityError(cap_col, "supply actual market capitalization; turnover is not market cap")
    work = factor_df.reset_index(drop=True)
    caps = cast(pd.Series, pd.to_numeric(work[cap_col], errors="coerce"))
    if (~np.isfinite(caps) | (caps <= 0)).any():
        raise FieldCapabilityError(cap_col, "market capitalization must be finite and positive for every input row")
    result = pd.Series(np.nan, index=work.index, name="factor_value")
    insufficient_sessions = []
    for date, group in work.groupby(session, sort=False):
        fv = group["factor_value"].to_numpy(dtype=float)
        log_cap = np.log(caps.loc[group.index].to_numpy(dtype=float))
        valid = np.isfinite(fv)
        # Keep the established minimum sample size, but never pretend a skipped
        # regression succeeded by returning the unneutralized factor.
        if valid.sum() < 5:
            insufficient_sessions.append(str(date))
            continue
        design = np.column_stack([np.ones(valid.sum()), log_cap[valid]])
        values = fv[valid]
        beta = np.linalg.lstsq(design, values, rcond=None)[0]
        result.loc[group.index[valid]] = values - design @ beta
    result.index = factor_df.index
    result.attrs["insufficient_sessions"] = insufficient_sessions
    return result


def neutralize_factor(
    factor_values: pd.Series,
    market_df: pd.DataFrame,
    industry: bool = False,
    market_cap: bool = False,
) -> pd.Series:
    """Apply neutralization to factor values.

    Args:
        factor_values: Factor values (same index as market_df).
        market_df: Market data DataFrame.
        industry: Whether to apply industry neutralization.
        market_cap: Whether to apply market-cap neutralization.

    Returns:
        Neutralized factor values.
    """
    if not industry and not market_cap:
        return factor_values

    if not factor_values.index.equals(market_df.index):
        if not factor_values.index.is_unique or not market_df.index.is_unique:
            raise ValueError("factor values and market rows require unambiguous aligned indexes")
        if len(factor_values) != len(market_df) or not market_df.index.isin(factor_values.index).all():
            raise ValueError("factor values must identify exactly the market data rows")
        aligned = factor_values.reindex(market_df.index)
    else:
        aligned = factor_values
    work = market_df.copy()
    work["factor_value"] = aligned
    attrs = dict(factor_values.attrs)

    if industry:
        # Fetching today's industry classification here is not a historical
        # point-in-time join. The data layer must supply the actual input.
        work["factor_value"] = industry_neutralize(work)

    if market_cap:
        residuals = cap_neutralize(work)
        work["factor_value"] = residuals
        attrs.update(residuals.attrs)

    result = cast(pd.Series, work["factor_value"]).copy()
    if not factor_values.index.equals(market_df.index):
        result = result.reindex(factor_values.index)
    result.name = factor_values.name
    result.attrs = attrs
    return result


def get_industry_data(stock_codes: list) -> pd.DataFrame | None:
    """Get industry classification for stocks from baostock.

    Uses Shenwan Level-1 industry classification. Caches per month.
    """
    cache_dir = _PROJECT_ROOT / "data" / "industry"
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Monthly cache
    month_key = time.strftime("%Y-%m")
    cache_path = cache_dir / f"industry_{month_key}.parquet"

    if cache_path.exists():
        try:
            df = pd.read_parquet(cache_path)
            if len(df) > 100:
                return df
        except Exception:
            pass

    # Fetch from baostock
    try:
        import baostock as bs
    except ImportError:
        logger.warning("baostock not installed, cannot fetch industry data")
        return None
    from .market_data import _baostock_call_guard, _baostock_login, _baostock_logout

    results = []
    with _bs_lock:
        try:
            _baostock_login()

            for code in stock_codes:
                try:
                    with _baostock_call_guard():
                        rs = bs.query_stock_industry(code=code)
                    while rs.error_code == "0" and rs.next():
                        row = rs.get_row_data()
                        if len(row) >= 4:
                            results.append({
                                "stock_code": row[1],
                                "industry": row[3],  # industry name
                                "industry_code": row[2] if len(row) > 2 else "",
                            })
                        break  # Only need one row per stock
                except Exception:
                    continue
        finally:
            _baostock_logout()

    if not results:
        return None

    df = pd.DataFrame(results)
    try:
        df.to_parquet(cache_path, index=False)
    except Exception:
        pass

    logger.info(f"Fetched industry data for {len(df)} stocks")
    return df
