"""Explicit free US exploration adapter; the demo cohort is not a security master."""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import Callable

import pandas as pd

from ..us_data.alpha_vantage import LIMITATIONS, AlphaVantageDailyProvider
from ..us_data.contracts import DataCapabilityError, PriceRequest, SecurityMapping, USDataProvider
from ..us_data.http import JSONClient
from .adapters import DataField, MarketCapabilities, adapter_registry


class USResearchAdapter:
    market = "us"
    requires_remote = True

    def __init__(self, provider: USDataProvider | None = None,
                 cohorts: dict[str, tuple[SecurityMapping, ...]] | None = None):
        self._provider = provider
        self.cohorts = cohorts if cohorts is not None else {
            "ibm_demo": (SecurityMapping("demo:IBM-common", "IBM", date(2000, 1, 1),
                                         identity_source="demo_only_not_security_master"),),
        }

    @property
    def provider(self) -> USDataProvider:
        if self._provider is None:
            self._provider = AlphaVantageDailyProvider(os.environ.get("ALPHAVANTAGE_API_KEY", "demo"))
        return self._provider

    def capabilities(self) -> MarketCapabilities:
        return MarketCapabilities(
            market=self.market, asset_class="equity", frequency="daily", universes=tuple(self.cohorts),
            benchmarks=("none",), default_benchmark="none", supports_short=False, supports_leverage=False,
            default_cost_bps=10.0,
            data_fields=tuple(DataField(
                name, "float", "raw daily observation; publication timing unverified",
                unit="USD/share" if name != "volume" else "shares",
                available_at="session_close_assumption_unverified", period="exchange_session",
                vintage="upstream_unknown; response_content_hash_retained", version="us_research_data/v1",
                status="research_only",
            ) for name in ("open", "high", "low", "close", "volume")),
            status="research_only", data_scope="explicit_fixed_cohort",
            provider=self._provider.name if self._provider else "alpha_vantage_free",
            capability_blockers=LIMITATIONS + ("benchmark_total_return_unavailable",),
        )

    def get_universe(self, universe: str, date: str | None = None) -> list[str]:
        if universe not in self.cohorts:
            raise DataCapabilityError("HISTORICAL_UNIVERSE_UNAVAILABLE", "Select an explicit registered US cohort.")
        return [security.security_id for security in self.cohorts[universe]]

    def fetch_market_data(self, universe: str, start_date: str, end_date: str,
                          universe_date: str | None = None) -> tuple[pd.DataFrame, list[str]]:
        return self.fetch_factor_market_data(universe, start_date, end_date, universe_date)

    def fetch_factor_market_data(self, universe: str, start_date: str, end_date: str,
                                 universe_date: str | None = None,
                                 cancel_check: Callable[[], None] | None = None) -> tuple[pd.DataFrame, list[str]]:
        codes = self.get_universe(universe, universe_date)
        provider = self.provider
        if isinstance(provider, AlphaVantageDailyProvider) and cancel_check is not None:
            provider = AlphaVantageDailyProvider(
                provider._api_key, calendar=provider.calendar,
                http=JSONClient(client=provider.http.client, cancel_check=cancel_check, gate=provider.http.gate),
            )
        frames = []
        provenance = []
        for security in self.cohorts[universe]:
            if cancel_check:
                cancel_check()
            batch = provider.fetch_prices(PriceRequest(
                security, date.fromisoformat(start_date), date.fromisoformat(end_date), datetime.now(timezone.utc),
            ))
            if batch.missing_sessions:
                raise DataCapabilityError("PRICE_COVERAGE_INCOMPLETE",
                                          f"Daily compact data lacks {len(batch.missing_sessions)} requested sessions.",
                                          next_action="shorten_explicit_research_window_or_configure_licensed_history")
            if not batch.prices.empty:
                frame = batch.prices.copy()
                frame.attrs = {}
                frames.append(frame)
                provenance.append(batch.provenance.__dict__)
        panel = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        panel.attrs["research_only"] = True
        panel.attrs["data_provenance"] = provenance
        panel.attrs["capability_blockers"] = list(self.capabilities().capability_blockers)
        if isinstance(provider, AlphaVantageDailyProvider):
            panel.attrs["calendar_sessions"] = [session.session.isoformat() for session in provider.calendar.sessions(
                date.fromisoformat(start_date), date.fromisoformat(end_date),
            )]
            panel.attrs["calendar_version"] = provider.calendar.version
        return panel, codes

    def fetch_benchmark_returns(self, benchmark: str, start_date: str, end_date: str) -> pd.Series:
        if benchmark != "none":
            raise DataCapabilityError("BENCHMARK_UNAVAILABLE", "No verified US total-return benchmark is configured.")
        return pd.Series(dtype=float, name="benchmark_unavailable")


adapter_registry.register(USResearchAdapter())
