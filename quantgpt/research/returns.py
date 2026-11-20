"""Shared raw-price return labels and compounded performance measures."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

import numpy as np
import pandas as pd

from .ledger import cash_entitlement, event_session, market_actions
from .market_inputs import price_basis


def annualized_return(returns: pd.Series, periods_per_year: int = 252) -> float:
    """Geometrically annualize observed net wealth, including every session."""
    if periods_per_year <= 0:
        raise ValueError("periods_per_year must be positive")
    values = returns.to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values < -1).any():
        raise ValueError("INVALID_RETURN_EVIDENCE: geometric annualization requires finite returns >= -1")
    if not len(values):
        return 0.0
    if (values == -1).any():
        return -1.0
    with np.errstate(over="ignore"):
        result = float(np.expm1(np.log1p(values).sum() * periods_per_year / len(values)))
    if not np.isfinite(result):
        raise ValueError("INVALID_RETURN_EVIDENCE: annualized wealth exceeds the finite numeric range")
    return result


def _event_records(frame: pd.DataFrame, sessions: pd.DatetimeIndex, end: pd.Timestamp) -> list[dict[str, Any]]:
    """Validate only events within the consumed price window, preserving same-day order."""
    events = market_actions(frame)
    securities = set(frame["stock_code"])
    first = frame["trade_date"].min()
    seen: set[str] = set()
    retired: set[str] = set()
    records = []
    for source in events:
        if not isinstance(source, Mapping):
            raise ValueError("INVALID_CORPORATE_ACTION: expected an event record")
        try:
            session = event_session(source)
            security = source["security_id"]
            if session > end or security not in securities:
                continue
            if session <= first and source.get("type") != "delisting":
                continue
            event_id = source["event_id"]
            kind = source["type"]
            if not isinstance(event_id, str) or not event_id.strip():
                raise ValueError("event_id must be a nonempty string")
            if session >= first and session not in sessions:
                raise ValueError("event session is absent from the shared calendar")
            if event_id in seen:
                raise ValueError(f"DUPLICATE_CORPORATE_ACTION: {event_id}")
            seen.add(event_id)
            record = {"session": session, "security_id": security, "type": kind}
            if kind == "split":
                ratio = float(source["ratio"])
                if not np.isfinite(ratio) or ratio <= 0:
                    raise ValueError("split ratio must be positive and finite")
                record["ratio"] = ratio
            elif kind in {"cash_dividend", "delisting"}:
                amount, _ = cash_entitlement(source, session)
                record["per_share"] = amount
                if kind == "delisting":
                    if security in retired:
                        raise ValueError(f"DUPLICATE_CORPORATE_ACTION: {security} has multiple terminal events")
                    retired.add(security)
            else:
                raise ValueError(f"UNSUPPORTED_CORPORATE_ACTION: {kind}")
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"INVALID_CORPORATE_ACTION: {exc}") from exc
        records.append(record)
    return sorted(records, key=lambda event: (event["session"], event["type"] == "delisting"))


def daily_asset_returns(frame: pd.DataFrame) -> pd.Series:
    """Close-to-close returns indexed by the end of the holding session."""
    events = market_actions(frame)
    covered_calendar = (frame.attrs.get("corporate_actions") is not None and "calendar_sessions" in frame.attrs
                        and price_basis(frame) in {"raw", "unknown"})
    if not events and not covered_calendar:
        # Existing parser inputs can be an ordered single-security close series.
        if "stock_code" not in frame:
            return cast(pd.Series, frame["close"]).pct_change(fill_method=None)
        return cast(pd.Series, frame["close"]).groupby(frame["stock_code"]).pct_change(fill_method=None)

    # Use an integer row identity since callers may retain duplicate index labels.
    ordered = cast(pd.DataFrame, frame[["trade_date", "stock_code", "close"]]).reset_index(drop=True)
    ordered = ordered.sort_values(["stock_code", "trade_date"])
    calendar = pd.DatetimeIndex(pd.to_datetime(frame.attrs.get("calendar_sessions", ordered["trade_date"].unique()))).sort_values()
    forward = forward_total_returns(ordered, 1)
    securities = ordered["stock_code"]
    position = pd.Series(calendar.get_indexer(pd.to_datetime(ordered["trade_date"])), index=ordered.index)
    adjacent = position.groupby(securities).diff().eq(1)
    trailing = forward.groupby(securities).shift().where(adjacent).sort_index()
    return pd.Series(trailing.to_numpy(), index=frame.index, name="daily_ret")


def forward_total_returns(frame: pd.DataFrame, horizon_sessions: int, *, evaluation_end: Any = None) -> pd.Series:
    """Return close-T to close-(T+N) wealth for one raw share held without trading.

    Splits change share quantity; cash dividends add cash/receivables without
    reinvestment. An ex-date on T precedes the hypothetical close purchase and
    gives no entitlement. Only supplied actions are used: no adjustment factors
    or price filling are inferred. A documented delisting converts the position
    to a cash claim at its terminal value. Other missing endpoints remain missing.
    """
    if horizon_sessions < 1:
        raise ValueError("horizon_sessions must be positive")
    if frame.empty:
        return pd.Series(index=frame.index, dtype=float, name="fwd_ret")
    if price_basis(frame) not in {"unknown", "raw"}:
        raise ValueError("INCOMPATIBLE_PRICE_BASIS: forward labels require raw close prices")
    work = cast(pd.DataFrame, frame[["trade_date", "stock_code", "close"]]).copy().reset_index(drop=True)
    work.attrs = frame.attrs.copy()
    work["trade_date"] = pd.to_datetime(work["trade_date"])
    if work[["trade_date", "stock_code"]].isna().to_numpy().any():
        raise ValueError("INVALID_RETURN_IDENTITY: security/session keys cannot be missing")
    if work.duplicated(["trade_date", "stock_code"]).any():
        raise ValueError("DUPLICATE_MARKET_ROW: forward labels require unique security/session keys")
    calendar = pd.DatetimeIndex(pd.to_datetime(frame.attrs.get("calendar_sessions", work["trade_date"].unique())))
    if calendar.hasnans or calendar.has_duplicates:
        raise ValueError("INVALID_SESSION_CALENDAR: sessions must be unique and non-missing")
    calendar = calendar.sort_values()
    if not pd.DatetimeIndex(work["trade_date"]).isin(calendar).all():
        raise ValueError("CALENDAR_CONTEXT_REQUIRED: input sessions are absent from the shared calendar")
    positions = calendar.get_indexer(work["trade_date"]) + horizon_sessions
    future_dates = pd.Series(pd.NaT, index=work.index, dtype=work["trade_date"].dtype)
    in_calendar = positions < len(calendar)
    future_dates.loc[in_calendar] = calendar.take(positions[in_calendar]).to_numpy()
    end = cast(pd.Timestamp, pd.Timestamp(evaluation_end) if evaluation_end is not None else work["trade_date"].max())
    future_dates = future_dates.where(future_dates <= end)
    endpoints = pd.MultiIndex.from_arrays([future_dates, work["stock_code"]])
    prices = cast(pd.Series, pd.to_numeric(work["close"], errors="coerce")).to_numpy(dtype=float)
    lookup = pd.Series(prices, index=pd.MultiIndex.from_frame(cast(pd.DataFrame, work[["trade_date", "stock_code"]])))
    future_prices = lookup.reindex(endpoints).to_numpy(dtype=float)
    quantities = np.ones(len(work))
    distributions = np.zeros(len(work))
    terminated = np.zeros(len(work), dtype=bool)
    eligible_start = np.ones(len(work), dtype=bool)
    rows_by_security = work.groupby("stock_code", sort=False).indices
    for event in _event_records(work, calendar, end):
        rows = rows_by_security[event["security_id"]]
        entitled = rows[((work["trade_date"].iloc[rows] < event["session"])
                         & (future_dates.iloc[rows] >= event["session"])).to_numpy()]
        if event["type"] == "split":
            quantities[entitled] *= event["ratio"]
        else:
            distributions[entitled] += quantities[entitled] * event["per_share"]
            if event["type"] == "delisting":
                quantities[entitled] = 0
                terminated[entitled] = True
                eligible_start[rows] &= (work["trade_date"].iloc[rows] < event["session"]).to_numpy()
    valid = eligible_start & np.isfinite(prices) & (prices > 0) & (terminated | (np.isfinite(future_prices) & (future_prices > 0)))
    future_prices = np.where(terminated, 0.0, future_prices)
    values = np.full(len(work), np.nan)
    values[valid] = (quantities[valid] * future_prices[valid] + distributions[valid]) / prices[valid] - 1
    if not np.isfinite(values[valid]).all():
        raise ValueError("INVALID_CORPORATE_ACTION: forward wealth exceeds the finite numeric range")
    return pd.Series(values, index=frame.index, name="fwd_ret")
