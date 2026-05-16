"""Demo global-equity adapter for Post-MVP market contract tests."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .adapters import DataField, MarketCapabilities, adapter_registry


class DemoGlobalEquityAdapter:
    market = "demo_global_equity"

    def capabilities(self) -> MarketCapabilities:
        return MarketCapabilities(
            market=self.market,
            asset_class="equity",
            frequency="daily",
            universes=("demo_small", "demo_large"),
            benchmarks=("demo_world",),
            default_benchmark="demo_world",
            supports_short=False,
            supports_leverage=False,
            default_cost_bps=10,
            data_fields=(
                DataField("open", "float", "open price"),
                DataField("high", "float", "high price"),
                DataField("low", "float", "low price"),
                DataField("close", "float", "close price"),
                DataField("volume", "float", "traded volume"),
                DataField("amount", "float", "traded amount"),
                DataField("pct_change", "float", "percentage change"),
                DataField("returns", "float", "daily return alias"),
                DataField("vwap", "float", "volume weighted average price"),
                DataField("cap", "float", "market capitalization alias"),
                DataField("market_cap", "float", "market capitalization"),
                DataField("industry", "string", "industry group"),
            ),
        )

    def get_universe(self, universe: str, date: str | None = None) -> list[str]:
        del date
        if universe == "demo_small":
            return ["DG.A", "DG.B", "DG.C", "DG.D"]
        if universe == "demo_large":
            return [f"DG.{idx:03d}" for idx in range(1, 21)]
        raise ValueError(f"Unsupported demo universe: {universe}")

    def fetch_market_data(self, universe: str, start_date: str, end_date: str, universe_date: str | None = None):
        del universe_date
        stock_codes = self.get_universe(universe)
        dates = pd.bdate_range(start_date, end_date)
        rows = []
        for idx, stock_code in enumerate(stock_codes):
            price = 20.0 + idx
            drift = 0.0005 + idx * 0.0001
            for date in dates:
                price *= 1 + drift
                volume = 1000 + idx * 100
                rows.append({
                    "trade_date": date,
                    "stock_code": stock_code,
                    "open": price,
                    "high": price * 1.01,
                    "low": price * 0.99,
                    "close": price,
                    "volume": volume,
                    "amount": price * volume,
                    "pct_change": drift * 100,
                    "returns": drift,
                    "vwap": price,
                    "cap": price * volume,
                    "market_cap": price * volume,
                    "industry": "demo",
                })
        return pd.DataFrame(rows), stock_codes

    def fetch_benchmark_returns(self, benchmark: str, start_date: str, end_date: str):
        if benchmark != "demo_world":
            raise ValueError(f"Unsupported demo benchmark: {benchmark}")
        dates = pd.bdate_range(start_date, end_date)
        return pd.Series(np.full(len(dates), 0.0004), index=dates, name=benchmark)


adapter_registry.register(DemoGlobalEquityAdapter())
