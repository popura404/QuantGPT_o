"""Shared factor value computation payloads for REST and MCP entrypoints."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from .backtest import api_context
from .backtest import compute_factor_values as compute_backtest_factor_values
from .market_data import MarketDataFetcher, get_universe
from .schemas import VALID_UNIVERSES

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


def validate_factor_values_request(
    expression: str,
    universe: str,
    start_date: str = "",
    end_date: str = "",
) -> NormalizedFactorValuesRequest:
    expression = expression.strip()
    if not expression:
        raise ValueError("expression must not be empty")
    if len(expression) > _MAX_EXPRESSION_LENGTH:
        raise ValueError(f"expression too long (max {_MAX_EXPRESSION_LENGTH} chars)")
    if universe not in VALID_UNIVERSES:
        raise ValueError(f"universe must be one of {sorted(VALID_UNIVERSES)}")

    end_dt = _parse_date(end_date, "end_date") if end_date else date.today()
    start_dt = _parse_date(start_date, "start_date") if start_date else end_dt - timedelta(days=365)
    if start_dt > end_dt:
        raise ValueError("start_date must be earlier than or equal to end_date")
    if (end_dt - start_dt).days > _MAX_DATE_RANGE_DAYS:
        raise ValueError(f"Date range too large (max {_MAX_DATE_RANGE_DAYS} days)")

    fetch_start = start_dt - timedelta(days=_WARMUP_DAYS)
    return NormalizedFactorValuesRequest(
        expression=expression,
        universe=universe,
        start_date=start_dt.isoformat(),
        end_date=end_dt.isoformat(),
        fetch_start=fetch_start.isoformat(),
    )


def compute_factor_values_payload(
    expression: str,
    universe: str = "csi500",
    start_date: str = "",
    end_date: str = "",
) -> dict:
    req = validate_factor_values_request(expression, universe, start_date, end_date)
    with api_context():
        stocks = get_universe(req.universe, date=req.start_date)
        if not stocks:
            raise ValueError(f"Empty universe: {req.universe}")

        fetcher = MarketDataFetcher()
        market_df = fetcher.fetch_stocks(stocks, req.fetch_start, req.end_date)
        if market_df is None or market_df.empty:
            raise ValueError("No market data available for this universe/date range")

        market_df = market_df.copy()
        market_df["factor_value"] = compute_backtest_factor_values(market_df, expression=req.expression)
        market_df["trade_date"] = pd.to_datetime(market_df["trade_date"])

        result_df = market_df[
            market_df["trade_date"] >= pd.Timestamp(req.start_date)
        ][["trade_date", "stock_code", "factor_value"]].copy()
        result_df = result_df.dropna(subset=["factor_value"]).sort_values(["trade_date", "stock_code"])

        dates_data = []
        for trade_date, group in result_df.groupby("trade_date", sort=True):
            values = {}
            for _, row in group.iterrows():
                value = row["factor_value"]
                if np.isfinite(value):
                    values[str(row["stock_code"])] = round(float(value), 6)
            if values:
                dates_data.append({
                    "date": pd.Timestamp(trade_date).strftime("%Y-%m-%d"),
                    "values": values,
                    "count": len(values),
                })

        return {
            "expression": req.expression,
            "universe": req.universe,
            "start_date": req.start_date,
            "end_date": req.end_date,
            "trading_days": len(dates_data),
            "data": dates_data,
        }


def _parse_date(value: str, field_name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be YYYY-MM-DD format") from exc
