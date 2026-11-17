"""US daily SIP prices and as-of SEC fundamentals for explicit security cohorts."""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import Callable

import pandas as pd

from ..us_data.alpaca import AlpacaDailyProvider
from ..us_data.alpha_vantage import AlphaVantageDailyProvider
from ..us_data.config import load_security_cohort
from ..us_data.contracts import DataCapabilityError, PriceRequest, SecurityMapping, USDataProvider
from ..us_data.fundamentals import DEFAULT_FIELDS, enrich_sec_fundamentals
from ..us_data.sec import FIELD_CONTRACTS, SECCompanyFactsProvider
from ..us_data.sec import LIMITATIONS as SEC_LIMITATIONS
from .adapters import DataField, MarketCapabilities, adapter_registry


class USResearchAdapter:
    market = "us"
    requires_remote = True

    def __init__(self, provider: USDataProvider | None = None,
                 cohorts: dict[str, tuple[SecurityMapping, ...]] | None = None, *,
                 issuer_ciks: dict[str, str] | None = None,
                 fundamental_fields: tuple[str, ...] | None = None,
                 fundamentals: SECCompanyFactsProvider | None = None):
        self._provider = provider
        self._cohorts = cohorts
        self.issuer_ciks = issuer_ciks or {}
        self._fundamental_fields = fundamental_fields
        self._fundamentals = fundamentals

    @property
    def provider_name(self) -> str:
        name = self._provider.name if self._provider else os.environ.get("QUANTGPT_US_PRICE_PROVIDER", "alpaca_sip")
        if name not in {"alpaca_sip", "alpha_vantage_free", "alpha_vantage"} and self._provider is None:
            raise DataCapabilityError("PROVIDER_UNSUPPORTED", "Select alpaca_sip or alpha_vantage for US prices.")
        return "alpha_vantage_free" if name == "alpha_vantage" else name

    @property
    def cohorts(self) -> dict[str, tuple[SecurityMapping, ...]]:
        if self._cohorts is None:
            configured = os.environ.get("QUANTGPT_US_SECURITIES_FILE")
            if configured:
                name, securities, self.issuer_ciks = load_security_cohort(configured)
                self._cohorts = {name: securities}
            elif self.provider_name == "alpha_vantage_free":
                self._cohorts = {"ibm_demo": (SecurityMapping("demo:IBM-common", "IBM", date(2000, 1, 1),
                                             identity_source="demo_only_not_security_master"),)}
            else:
                self._cohorts = {}
        return self._cohorts

    @property
    def fundamental_fields(self) -> tuple[str, ...]:
        if self._fundamental_fields is not None:
            fields = self._fundamental_fields
        else:
            configured = os.environ.get("QUANTGPT_US_FUNDAMENTAL_FIELDS")
            if configured is not None:
                fields = tuple(name.strip() for name in configured.split(",") if name.strip())
            else:
                fields = DEFAULT_FIELDS if self.provider_name == "alpaca_sip" or self.issuer_ciks else ()
        if len(set(fields)) != len(fields) or any(name not in FIELD_CONTRACTS for name in fields):
            raise DataCapabilityError("SEC_FIELD_UNSUPPORTED", "Select unique fields from the SEC field catalog.")
        return fields

    @property
    def provider(self) -> USDataProvider:
        if self._provider is None:
            self._provider = (AlpacaDailyProvider() if self.provider_name == "alpaca_sip" else
                              AlphaVantageDailyProvider(os.environ.get("ALPHAVANTAGE_API_KEY", "demo")))
        return self._provider

    def capabilities(self) -> MarketCapabilities:
        universes = tuple(self.cohorts)
        if self._provider is not None:
            info = self._provider.capabilities()
        elif self.provider_name == "alpaca_sip":
            from ..us_data.alpaca import LIMITATIONS
            info = {"limitations": LIMITATIONS}
        else:
            from ..us_data.alpha_vantage import LIMITATIONS
            info = {"limitations": LIMITATIONS}
        price_availability = ("next_calendar_day_0015_America_New_York" if self.provider_name == "alpaca_sip"
                              else "session_close_assumption_unverified")
        fundamentals = tuple(DataField(name, "float", f"SEC {FIELD_CONTRACTS[name][0]}:{FIELD_CONTRACTS[name][1]}",
            unit=FIELD_CONTRACTS[name][2], available_at="filing_date_plus_one_calendar_day_ny_midnight",
            period="quarter" if name in {"net_income", "diluted_eps_period"} else "instant",
            vintage="accession_asof_decision", version="sec_companyfacts/v1", status="research_only")
            for name in self.fundamental_fields)
        return MarketCapabilities(
            market=self.market, asset_class="equity", frequency="daily", universes=universes,
            benchmarks=("none",), default_benchmark="none", supports_short=False, supports_leverage=False,
            default_cost_bps=10.0,
            data_fields=tuple(DataField(
                name, "float", "raw daily observation",
                unit="USD/share" if name != "volume" else "shares",
                available_at=price_availability, period="exchange_session",
                vintage="upstream_unknown; response_content_hash_retained", version="us_research_data/v1",
                status="research_only",
            ) for name in ("open", "high", "low", "close", "volume")) + fundamentals,
            status="research_only", data_scope="explicit_fixed_cohort",
            provider=self.provider_name,
            capability_blockers=tuple(info.get("limitations", ())) + (SEC_LIMITATIONS if fundamentals else ())
                                + ("benchmark_total_return_unavailable",),
        )

    def get_universe(self, universe: str, date: str | None = None) -> list[str]:
        if universe not in self.cohorts:
            raise DataCapabilityError("HISTORICAL_UNIVERSE_UNAVAILABLE",
                                      "Configure QUANTGPT_US_SECURITIES_FILE with an explicit US cohort.")
        return [security.security_id for security in self.cohorts[universe]]

    def fetch_market_data(self, universe: str, start_date: str, end_date: str,
                          universe_date: str | None = None) -> tuple[pd.DataFrame, list[str]]:
        return self.fetch_factor_market_data(universe, start_date, end_date, universe_date)

    def fetch_factor_market_data(self, universe: str, start_date: str, end_date: str,
                                 universe_date: str | None = None,
                                 cancel_check: Callable[[], None] | None = None, *,
                                 asof: datetime | None = None) -> tuple[pd.DataFrame, list[str]]:
        codes = self.get_universe(universe, universe_date)
        fields = self.fundamental_fields
        fundamentals = None
        if fields:
            if any(code not in self.issuer_ciks for code in codes):
                raise DataCapabilityError("SEC_CIK_REQUIRED", "Every security requires an explicit issuer_cik.")
            fundamentals = self._fundamentals or SECCompanyFactsProvider(os.environ.get("SEC_USER_AGENT", ""))
        provider = self.provider
        if cancel_check is not None and isinstance(provider, (AlpacaDailyProvider, AlphaVantageDailyProvider)):
            import copy
            provider = copy.copy(provider)
            provider.http = copy.copy(provider.http)
            provider.http.cancel_check = cancel_check
            if fundamentals is not None:
                fundamentals = copy.copy(fundamentals)
                fundamentals.http = copy.copy(fundamentals.http)
                fundamentals.http.cancel_check = cancel_check
        frames = []
        provenance = []
        for security in self.cohorts[universe]:
            if cancel_check:
                cancel_check()
            batch = provider.fetch_prices(PriceRequest(
                security, date.fromisoformat(start_date), date.fromisoformat(end_date), asof or datetime.now(timezone.utc),
                feed="sip" if self.provider_name == "alpaca_sip" else "TIME_SERIES_DAILY:compact",
            ))
            if batch.missing_sessions:
                raise DataCapabilityError("PRICE_COVERAGE_INCOMPLETE",
                                          f"Daily prices lack {len(batch.missing_sessions)} requested sessions.",
                                          next_action="check_requested_dates_and_provider_coverage")
            if not batch.prices.empty:
                frame = batch.prices.copy()
                frame.attrs = {}
                if self.provider_name == "alpaca_sip":
                    frame["decision_at"] = frame["available_at"]
                frames.append(frame)
                provenance.append(batch.provenance.__dict__)
        panel = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        panel.attrs["research_only"] = True
        panel.attrs["data_provenance"] = provenance
        panel.attrs["capability_blockers"] = list(self.capabilities().capability_blockers)
        if isinstance(provider, (AlpacaDailyProvider, AlphaVantageDailyProvider)):
            panel.attrs["calendar_sessions"] = [session.session.isoformat() for session in provider.calendar.sessions(
                date.fromisoformat(start_date), date.fromisoformat(end_date),
            )]
            panel.attrs["calendar_version"] = provider.calendar.version
        if fundamentals is not None:
            panel = enrich_sec_fundamentals(panel, self.issuer_ciks, provider=fundamentals, fields=fields)
        return panel, codes

    def fetch_benchmark_returns(self, benchmark: str, start_date: str, end_date: str) -> pd.Series:
        if benchmark != "none":
            raise DataCapabilityError("BENCHMARK_UNAVAILABLE", "No verified US total-return benchmark is configured.")
        return pd.Series(dtype=float, name="benchmark_unavailable")


adapter_registry.register(USResearchAdapter())
