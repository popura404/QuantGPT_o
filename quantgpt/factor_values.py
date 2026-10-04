"""Shared factor value computation payloads for REST and MCP entrypoints."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable, cast

import numpy as np
import pandas as pd

from .backtest import api_context
from .backtest import compute_factor_values as compute_backtest_factor_values
from .market_data import MarketDataFetcher, get_universe
from .schemas import VALID_UNIVERSES
from .strategy.adapters import get_adapter
from .us_data.contracts import DataCapabilityError

_MAX_EXPRESSION_LENGTH = 2000
_MAX_DATE_RANGE_DAYS = 750
_WARMUP_DAYS = 260


@dataclass(frozen=True)
class NormalizedFactorValuesRequest:
    expression: str
    universe: str
    start_date: str
    end_date: str
    fetch_start: str
    market: str = "a_share"
    backend: str = "local"


def validate_factor_values_request(
    expression: str,
    universe: str,
    start_date: str = "",
    end_date: str = "",
    market: str = "a_share",
    backend: str = "local",
) -> NormalizedFactorValuesRequest:
    expression = expression.strip()
    if not expression:
        raise ValueError("expression must not be empty")
    if len(expression) > _MAX_EXPRESSION_LENGTH:
        raise ValueError(f"expression too long (max {_MAX_EXPRESSION_LENGTH} chars)")
    if backend != "local":
        raise DataCapabilityError("BACKEND_UNSUPPORTED", "Factor values require a local market adapter; WQ does not expose this local series.")
    valid_universes = VALID_UNIVERSES if market == "a_share" else get_adapter(market).capabilities().universes
    if universe not in valid_universes:
        raise ValueError(f"universe must be one of {sorted(valid_universes)}")

    end_dt = _parse_date(end_date, "end_date") if end_date else date.today()
    start_dt = _parse_date(start_date, "start_date") if start_date else end_dt - timedelta(days=365)
    if start_dt > end_dt:
        raise ValueError("start_date must be earlier than or equal to end_date")
    if (end_dt - start_dt).days > _MAX_DATE_RANGE_DAYS:
        raise ValueError(f"Date range too large (max {_MAX_DATE_RANGE_DAYS} days)")

    fetch_start = start_dt - timedelta(days=_WARMUP_DAYS)
    if market == "us":
        from .expression_parser import infer_expression_lookback
        from .us_data.calendar import NewYorkCalendar

        lookback = infer_expression_lookback(expression)
        if lookback.recursive:
            raise DataCapabilityError("RECURSIVE_WARMUP_UNFROZEN", "Freeze a recursive operator's initialization before free-feed research.")
        calendar = NewYorkCalendar()
        prior = [session.session for session in calendar.sessions(
            start_dt - timedelta(days=max(370, lookback.prior_sessions * 3)), start_dt,
        ) if session.session < start_dt]
        if lookback.prior_sessions > len(prior):
            raise DataCapabilityError("WARMUP_UNAVAILABLE", "Insufficient exchange sessions for expression warmup.")
        fetch_start = prior[-lookback.prior_sessions] if lookback.prior_sessions else start_dt
    return NormalizedFactorValuesRequest(
        expression=expression,
        universe=universe,
        start_date=start_dt.isoformat(),
        end_date=end_dt.isoformat(),
        fetch_start=fetch_start.isoformat(),
        market=market,
        backend=backend,
    )


def compute_factor_values_payload(
    expression: str,
    universe: str = "csi500",
    start_date: str = "",
    end_date: str = "",
    allow_remote_fetch: bool = True,
    universe_date: str | None = None,
    cancel_check: Callable[[], None] | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
    market: str = "a_share",
    backend: str = "local",
) -> dict:
    req = validate_factor_values_request(expression, universe, start_date, end_date, market=market, backend=backend)
    with api_context():
        cache_only = not allow_remote_fetch
        resolved_universe_date = universe_date or req.end_date
        adapter = get_adapter(req.market) if req.market != "a_share" else None
        if adapter is not None:
            if cache_only and getattr(adapter, "requires_remote", False):
                raise DataCapabilityError("REMOTE_FETCH_DISABLED", "This US provider has no local snapshot selected.",
                                          next_action="select_frozen_snapshot_or_allow_remote_fetch")
            from .fundamental_data import detect_fundamental_vars

            missing = detect_fundamental_vars(req.expression) - {field.name for field in adapter.capabilities().data_fields}
            if missing:
                raise DataCapabilityError("FIELD_UNAVAILABLE", f"Market adapter lacks required fields: {', '.join(sorted(missing))}")
            stocks = adapter.get_universe(req.universe, date=resolved_universe_date)
        else:
            stocks = get_universe(req.universe, date=resolved_universe_date, cache_only=cache_only)
        if not stocks:
            raise ValueError(
                f"Empty universe: {req.universe}. "
                "MCP calls default to local cache; pass allow_remote_fetch=true or prewarm the universe cache."
            )

        if cancel_check:
            cancel_check()
        if adapter is not None:
            factor_fetch = getattr(adapter, "fetch_factor_market_data", None)
            if callable(factor_fetch):
                market_df, stocks = cast(tuple[pd.DataFrame, list[str]], factor_fetch(
                    req.universe, req.fetch_start, req.end_date,
                    universe_date=resolved_universe_date, cancel_check=cancel_check,
                ))
            else:
                market_df, stocks = adapter.fetch_market_data(req.universe, req.fetch_start, req.end_date,
                                                            universe_date=resolved_universe_date)
        else:
            market_df = MarketDataFetcher().fetch_stocks(
                stocks, req.fetch_start, req.end_date, cache_only=cache_only,
                cancel_check=cancel_check, progress_callback=progress_callback,
            )
        if cancel_check:
            cancel_check()
        if market_df is None or market_df.empty:
            raise ValueError(
                "No market data available for this universe/date range. "
                "MCP calls default to local cache; pass allow_remote_fetch=true or prewarm the stock cache."
            )

        market_df = cast(pd.DataFrame, market_df).copy()
        market_df["factor_value"] = compute_backtest_factor_values(market_df, expression=req.expression)
        market_df["trade_date"] = pd.to_datetime(market_df["trade_date"])

        result_df = cast(pd.DataFrame, market_df[
            market_df["trade_date"] >= pd.Timestamp(req.start_date)
        ][["trade_date", "stock_code", "factor_value"]]).copy()
        result_df = result_df.dropna(subset=["factor_value"]).sort_values(["trade_date", "stock_code"])

        dates_data = []
        for trade_date, group in result_df.groupby("trade_date", sort=True):
            values = {}
            for _, row in group.iterrows():
                value = cast(float, row["factor_value"])
                if np.isfinite(value):
                    values[str(row["stock_code"])] = round(float(value), 6)
            if values:
                dates_data.append({
                    "date": pd.Timestamp(str(trade_date)).strftime("%Y-%m-%d"),
                    "values": values,
                    "count": len(values),
                })

        payload = {
            "expression": req.expression,
            "universe": req.universe,
            "start_date": req.start_date,
            "end_date": req.end_date,
            "universe_date": resolved_universe_date,
            "trading_days": len(dates_data),
            "data": dates_data,
        }
        if adapter is not None:
            payload.update({"market": req.market, "backend": req.backend,
                            "status": adapter.capabilities().status,
                            "capability_blockers": list(adapter.capabilities().capability_blockers),
                            "data_provenance": market_df.attrs.get("data_provenance", []),
                            "research_only": market_df.attrs.get("research_only", True)})
        return payload


def _parse_date(value: str, field_name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be YYYY-MM-DD format") from exc
