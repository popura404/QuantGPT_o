"""Shared raw-price return labels and compounded performance measures."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd


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


def _event_records(frame: pd.DataFrame, sessions: pd.DatetimeIndex) -> list[dict[str, Any]]:
    """Validate only events within the consumed price window, preserving same-day order."""
    events = frame.attrs.get("corporate_actions", [])
    if not isinstance(events, (list, tuple)):
        raise ValueError("INVALID_CORPORATE_ACTION: corporate_actions must be an event sequence")
    securities = set(frame["stock_code"])
    first, last = frame["trade_date"].min(), frame["trade_date"].max()
    seen: set[str] = set()
    records = []
    for source in events:
        if not isinstance(source, Mapping):
            raise ValueError("INVALID_CORPORATE_ACTION: expected an event record")
        try:
            session = pd.Timestamp(source["session"])
            security = source["security_id"]
            if pd.isna(session):
                raise ValueError("missing event session")
            if session <= first or session > last or security not in securities:
                continue
            event_id = source["event_id"]
            kind = source["type"]
            if not isinstance(event_id, str) or not event_id.strip():
                raise ValueError("event_id must be a nonempty string")
            if session not in sessions:
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
            elif kind == "cash_dividend":
                amount = float(source["per_share"])
                pay_session = pd.Timestamp(source["pay_session"])
                if not np.isfinite(amount) or amount < 0 or pd.isna(pay_session) or pay_session < session:
                    raise ValueError("dividend must be nonnegative with a payment date on/after its ex-date")
                record["per_share"] = amount
            else:
                raise ValueError(f"UNSUPPORTED_CORPORATE_ACTION: {kind}")
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"INVALID_CORPORATE_ACTION: {exc}") from exc
        records.append(record)
    return sorted(records, key=lambda event: event["session"])


def forward_total_returns(frame: pd.DataFrame, horizon_sessions: int) -> pd.Series:
    """Return close-T to close-(T+N) wealth for one raw share held without trading.

    Splits change share quantity; cash dividends add cash/receivables without
    reinvestment. An ex-date on T precedes the hypothetical close purchase and
    gives no entitlement. Only supplied actions are used: no adjustment factors
    or price filling are inferred. Missing endpoints remain missing labels.
    """
    if horizon_sessions < 1:
        raise ValueError("horizon_sessions must be positive")
    if frame.empty:
        return pd.Series(index=frame.index, dtype=float, name="fwd_ret")
    snapshot = frame.attrs.get("data_snapshot") or {}
    source = frame.attrs.get("source_metadata") or {}
    adjustment = (frame.attrs.get("adjustment") or source.get("adjustment")
                  or (snapshot.get("query_params") or {}).get("adjustment_type"))
    if adjustment not in {None, "unknown", "raw", "none", "unadjusted"}:
        raise ValueError("INCOMPATIBLE_PRICE_BASIS: forward labels require raw close prices")
    work = frame[["trade_date", "stock_code", "close"]].copy().reset_index(drop=True)
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
    if not work["trade_date"].isin(calendar).all():
        raise ValueError("CALENDAR_CONTEXT_REQUIRED: input sessions are absent from the shared calendar")
    positions = calendar.get_indexer(work["trade_date"]) + horizon_sessions
    future_dates = pd.Series(pd.NaT, index=work.index, dtype=work["trade_date"].dtype)
    in_calendar = positions < len(calendar)
    future_dates.loc[in_calendar] = calendar.take(positions[in_calendar]).to_numpy()
    endpoints = pd.MultiIndex.from_arrays([future_dates, work["stock_code"]])
    prices = pd.to_numeric(work["close"], errors="coerce").to_numpy(dtype=float)
    lookup = pd.Series(prices, index=pd.MultiIndex.from_frame(work[["trade_date", "stock_code"]]))
    future_prices = lookup.reindex(endpoints).to_numpy(dtype=float)
    quantities = np.ones(len(work))
    distributions = np.zeros(len(work))
    rows_by_security = work.groupby("stock_code", sort=False).indices
    for event in _event_records(work, calendar):
        rows = rows_by_security[event["security_id"]]
        entitled = rows[((work["trade_date"].iloc[rows] < event["session"])
                         & (future_dates.iloc[rows] >= event["session"])).to_numpy()]
        if event["type"] == "split":
            quantities[entitled] *= event["ratio"]
        else:
            distributions[entitled] += quantities[entitled] * event["per_share"]
    valid = np.isfinite(prices) & (prices > 0) & np.isfinite(future_prices) & (future_prices > 0)
    values = np.full(len(work), np.nan)
    values[valid] = (quantities[valid] * future_prices[valid] + distributions[valid]) / prices[valid] - 1
    if not np.isfinite(values[valid]).all():
        raise ValueError("INVALID_CORPORATE_ACTION: forward wealth exceeds the finite numeric range")
    return pd.Series(values, index=frame.index, name="fwd_ret")
