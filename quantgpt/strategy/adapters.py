"""Market/data adapter contracts for the strategy framework."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class DataField:
    name: str
    dtype: str
    description: str = ""


@dataclass(frozen=True)
class MarketCapabilities:
    market: str
    asset_class: str
    frequency: str
    universes: tuple[str, ...]
    benchmarks: tuple[str, ...]
    default_benchmark: str
    supports_short: bool
    supports_leverage: bool
    default_cost_bps: float
    data_fields: tuple[DataField, ...]

    def to_dict(self) -> dict:
        return {
            "market": self.market,
            "asset_class": self.asset_class,
            "frequency": self.frequency,
            "universes": list(self.universes),
            "benchmarks": list(self.benchmarks),
            "default_benchmark": self.default_benchmark,
            "supports_short": self.supports_short,
            "supports_leverage": self.supports_leverage,
            "default_cost_bps": self.default_cost_bps,
            "data_fields": [field.__dict__ for field in self.data_fields],
        }


class MarketAdapter(Protocol):
    market: str

    def capabilities(self) -> MarketCapabilities:
        ...

    def get_universe(self, universe: str, date: str | None = None) -> list[str]:
        ...

    def fetch_market_data(self, universe: str, start_date: str, end_date: str, universe_date: str | None = None):
        ...

    def fetch_benchmark_returns(self, benchmark: str, start_date: str, end_date: str):
        ...


class AdapterRegistry:
    def __init__(self):
        self._adapters: dict[str, MarketAdapter] = {}

    def register(self, adapter: MarketAdapter) -> None:
        self._adapters[adapter.market] = adapter

    def get(self, market: str) -> MarketAdapter:
        try:
            return self._adapters[market]
        except KeyError as exc:
            raise ValueError(f"Unsupported market: {market}") from exc

    def list_markets(self) -> list[dict]:
        return [adapter.capabilities().to_dict() for adapter in self._adapters.values()]

    def list_data_fields(self, market: str) -> list[dict]:
        caps = self.get(market).capabilities()
        return [field.__dict__ for field in caps.data_fields]


adapter_registry = AdapterRegistry()


def get_adapter(market: str) -> MarketAdapter:
    return adapter_registry.get(market)


def list_markets() -> list[dict]:
    return adapter_registry.list_markets()


def list_data_fields(market: str = "a_share") -> list[dict]:
    return adapter_registry.list_data_fields(market)
