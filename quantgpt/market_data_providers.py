"""Optional market-data provider registry.

This module defines the provider contract without making paid SDKs hard runtime
dependencies. The default backtest path still uses ``market_data.py`` directly.
"""

from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass
from typing import Any, Protocol

PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"


class MarketDataProvider(Protocol):
    name: str
    capabilities: tuple[str, ...]

    def credential_status(self) -> dict[str, Any]:
        ...

    def fetch_stocks(self, *args: Any, **kwargs: Any) -> Any:
        ...

    def fetch_benchmark(self, *args: Any, **kwargs: Any) -> Any:
        ...

    def fetch_universe(self, *args: Any, **kwargs: Any) -> Any:
        ...

    def fetch_fundamentals(self, *args: Any, **kwargs: Any) -> Any:
        ...

    def fetch_industry(self, *args: Any, **kwargs: Any) -> Any:
        ...

    def source_metadata(self) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    capabilities: tuple[str, ...]
    credential_env_vars: tuple[str, ...] = ()
    sdk_module: str | None = None
    vendor_kind: str = "optional_paid"


class OptionalProvider:
    def __init__(self, spec: ProviderSpec):
        self.spec = spec
        self.name = spec.name
        self.capabilities = spec.capabilities

    def credential_status(self) -> dict[str, Any]:
        missing_env = [name for name in self.spec.credential_env_vars if not os.environ.get(name)]
        sdk_available = True if self.spec.sdk_module is None else importlib.util.find_spec(self.spec.sdk_module) is not None
        return {
            "provider": self.name,
            "credentials_required": list(self.spec.credential_env_vars),
            "credentials_configured": not missing_env,
            "missing_env_vars": missing_env,
            "sdk_module": self.spec.sdk_module,
            "sdk_available": sdk_available,
            "available": not missing_env and sdk_available,
        }

    def source_metadata(self) -> dict[str, Any]:
        status = self.credential_status()
        return {
            "vendor": self.name,
            "vendor_kind": self.spec.vendor_kind,
            "capabilities": list(self.capabilities),
            "credential_status": status,
        }

    def fetch_stocks(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._unavailable("fetch_stocks")

    def fetch_benchmark(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._unavailable("fetch_benchmark")

    def fetch_universe(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._unavailable("fetch_universe")

    def fetch_fundamentals(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._unavailable("fetch_fundamentals")

    def fetch_industry(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._unavailable("fetch_industry")

    def _unavailable(self, capability: str) -> dict[str, Any]:
        status = self.credential_status()
        reason = "SDK_MISSING" if status["credentials_configured"] and not status["sdk_available"] else "CREDENTIALS_MISSING"
        if status["available"]:
            reason = "ADAPTER_NOT_IMPLEMENTED"
        return {
            "error_code": PROVIDER_UNAVAILABLE,
            "provider": self.name,
            "capability": capability,
            "reason": reason,
            "credential_status": status,
        }


class ProviderRegistry:
    def __init__(self):
        self._providers: dict[str, MarketDataProvider] = {}

    def register(self, provider: MarketDataProvider) -> None:
        self._providers[provider.name] = provider

    def get(self, name: str) -> MarketDataProvider:
        try:
            return self._providers[name]
        except KeyError as exc:
            raise ValueError(f"Unknown market data provider: {name}") from exc

    def list_providers(self) -> list[dict[str, Any]]:
        return [
            {
                "name": provider.name,
                "capabilities": list(provider.capabilities),
                "credential_status": provider.credential_status(),
                "source_metadata": provider.source_metadata(),
            }
            for provider in self._providers.values()
        ]


def default_provider_registry() -> ProviderRegistry:
    registry = ProviderRegistry()
    for spec in DEFAULT_PROVIDER_SPECS:
        registry.register(OptionalProvider(spec))
    return registry


DEFAULT_PROVIDER_SPECS = (
    ProviderSpec(
        name="local_cache",
        capabilities=("ohlcv", "benchmark_returns", "universe"),
        vendor_kind="cache",
    ),
    ProviderSpec(
        name="baostock",
        capabilities=("ohlcv", "historical_constituents", "fundamentals"),
        sdk_module="baostock",
        vendor_kind="free_source",
    ),
    ProviderSpec(
        name="rqdatac",
        capabilities=(
            "ohlcv",
            "historical_constituents",
            "suspension_flags",
            "adjusted_prices",
            "industry_classification",
        ),
        credential_env_vars=("RQDATAC_USERNAME", "RQDATAC_PASSWORD"),
        sdk_module="rqdatac",
    ),
    ProviderSpec(
        name="tushare",
        capabilities=("ohlcv", "limit_up_down", "financial_statement_fields", "announcement_date"),
        credential_env_vars=("TUSHARE_TOKEN",),
        sdk_module="tushare",
    ),
    ProviderSpec(
        name="wind",
        capabilities=("ohlcv", "historical_constituents", "industry_classification", "financial_statement_fields"),
        credential_env_vars=("WIND_USERNAME", "WIND_PASSWORD"),
        sdk_module="WindPy",
    ),
    ProviderSpec(
        name="choice",
        capabilities=("ohlcv", "historical_constituents", "industry_classification"),
        credential_env_vars=("CHOICE_USERNAME", "CHOICE_PASSWORD"),
    ),
    ProviderSpec(
        name="jqdata",
        capabilities=("ohlcv", "historical_constituents", "suspension_flags", "limit_up_down"),
        credential_env_vars=("JQDATA_USERNAME", "JQDATA_PASSWORD"),
        sdk_module="jqdatasdk",
    ),
)


provider_registry = default_provider_registry()


def list_market_data_providers() -> list[dict[str, Any]]:
    return provider_registry.list_providers()


def get_market_data_provider(name: str) -> MarketDataProvider:
    return provider_registry.get(name)
