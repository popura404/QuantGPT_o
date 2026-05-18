"""A-share strategy adapter wrapping existing market data capabilities."""

from __future__ import annotations

import pandas as pd

from .adapters import DataField, MarketCapabilities, adapter_registry


class AShareAdapter:
    market = "a_share"

    def capabilities(self) -> MarketCapabilities:
        from ..fundamental_data import ALL_FUNDAMENTAL_NAMES
        from ..market_data import BENCHMARK_CODES, UNIVERSES

        base_fields = [
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
        ]
        fundamental_fields = [
            DataField(name, "float", "fundamental field")
            for name in sorted(ALL_FUNDAMENTAL_NAMES)
        ]
        universes = tuple(sorted(set(UNIVERSES) | {"hs300", "csi500", "csi1000", "csi2000"}))
        benchmarks = tuple(sorted(BENCHMARK_CODES))
        return MarketCapabilities(
            market="a_share",
            asset_class="equity",
            frequency="daily",
            universes=universes,
            benchmarks=benchmarks,
            default_benchmark="hs300",
            supports_short=False,
            supports_leverage=False,
            default_cost_bps=30,
            data_fields=tuple(base_fields + fundamental_fields),
        )

    def get_universe(self, universe: str, date: str | None = None) -> list[str]:
        from ..market_data import get_universe

        return get_universe(universe, date=date)

    def fetch_market_data(
        self,
        universe: str,
        start_date: str,
        end_date: str,
        universe_date: str | None = None,
    ) -> tuple[pd.DataFrame, list[str]]:
        from ..market_data import MarketDataFetcher

        stock_codes = self.get_universe(universe, date=universe_date or start_date)
        fetcher = MarketDataFetcher()
        market_df = fetcher.fetch_stocks(stock_codes, start_date, end_date)
        if market_df is None:
            market_df = pd.DataFrame()
        return market_df, stock_codes

    def fetch_benchmark_returns(self, benchmark: str, start_date: str, end_date: str) -> pd.Series:
        from ..market_data import fetch_benchmark_returns

        return fetch_benchmark_returns(benchmark, start_date, end_date)


adapter_registry.register(AShareAdapter())
