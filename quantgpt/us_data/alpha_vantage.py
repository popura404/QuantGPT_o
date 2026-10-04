"""Official free daily endpoint, limited to exploration until event coverage exists."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date
from typing import Any

import pandas as pd

from .calendar import NewYorkCalendar
from .contracts import DataCapabilityError, DataProvenance, PriceBatch, PriceRequest, SecurityMapping
from .http import JSONClient

LIMITATIONS = (
    "compact_history_only", "corporate_actions_unavailable", "historical_membership_unavailable",
    "delisting_terminal_values_unavailable", "security_master_unverified", "upstream_vintages_unknown",
    "session_close_availability_assumption_unverified",
)


class AlphaVantageDailyProvider:
    name = "alpha_vantage_free"

    def __init__(self, api_key: str = "demo", *, http: JSONClient | None = None, calendar: NewYorkCalendar | None = None):
        self._api_key = api_key
        self.http = http or JSONClient()
        self.calendar = calendar or NewYorkCalendar()

    def capabilities(self) -> dict[str, Any]:
        return {"provider": self.name, "market": "us", "status": "research_only", "ohlcv": True,
                "raw_prices": True, "corporate_actions": False, "historical_universe": False,
                "pagination": "not_supported", "feed": "TIME_SERIES_DAILY:compact", "currency": "USD",
                "demo": self._api_key == "demo", "calendar_version": self.calendar.version,
                "limitations": list(LIMITATIONS), "license": "personal_noncommercial_per_provider_terms"}

    def fetch_prices(self, request: PriceRequest) -> PriceBatch:
        request.validate()
        ticker = request.security.ticker.upper()
        if not re.fullmatch(r"[A-Z0-9.-]{1,20}", ticker):
            raise ValueError("unsupported US ticker syntax")
        if self._api_key == "demo" and ticker != "IBM":
            raise DataCapabilityError("DEMO_SYMBOL_UNSUPPORTED", "The public daily demo is limited to IBM.",
                                      next_action="configure_free_alpha_vantage_key")
        response = self.http.get("https://www.alphavantage.co/query", params={
            "function": "TIME_SERIES_DAILY", "symbol": ticker, "apikey": self._api_key,
        })
        payload = response.data
        if "Note" in payload or "Information" in payload:
            raise DataCapabilityError("PROVIDER_LIMIT_OR_ENTITLEMENT", "Provider returned a quota or entitlement message.",
                                      retryable=True, next_action="check_provider_quota_or_key")
        if "Error Message" in payload:
            raise DataCapabilityError("PROVIDER_REQUEST_REJECTED", "Provider rejected the daily-data request.")
        data = payload.get("Time Series (Daily)")
        if not isinstance(data, dict) or not data:
            raise DataCapabilityError("PROVIDER_SCHEMA_ERROR", "Daily time series is absent or empty.")
        metadata = payload.get("Meta Data", {})
        if str(metadata.get("2. Symbol", "")).upper() != ticker:
            raise DataCapabilityError("PROVIDER_IDENTITY_MISMATCH", "Provider ticker differs from the requested mapping.")
        sessions = self.calendar.sessions(request.start, request.end)
        eligible = [session for session in sessions if session.closes_at <= request.asof]
        rows = []
        missing = []
        for session in eligible:
            self.http.cancel_check()
            label = session.session.isoformat()
            raw = data.get(label)
            if raw is None:
                missing.append(label)
                continue
            try:
                o, h, low, close, volume = (float(raw[name]) for name in (
                    "1. open", "2. high", "3. low", "4. close", "5. volume"))
            except (TypeError, ValueError, KeyError) as exc:
                raise DataCapabilityError("PROVIDER_SCHEMA_ERROR", "Daily OHLCV fields are malformed.") from exc
            if not all(math.isfinite(x) for x in (o, h, low, close, volume)) or min(o, h, low, close) <= 0 or volume < 0:
                raise DataCapabilityError("INVALID_OHLCV", "Daily values must be finite and prices positive.")
            if not low <= min(o, close) <= max(o, close) <= h:
                raise DataCapabilityError("INVALID_OHLCV", "Daily high/low do not contain open and close.")
            rows.append({"security_id": request.security.security_id, "stock_code": request.security.security_id,
                         "ticker": ticker, "trade_date": pd.Timestamp(label), "session": label,
                         "open": o, "high": h, "low": low, "close": close, "volume": volume,
                         "available_at": session.closes_at.isoformat(), "currency": "USD", "adjustment": "raw"})
        request_identity = {"security_id": request.security.security_id, "ticker": ticker,
                            "start": request.start.isoformat(), "end": request.end.isoformat(),
                            "asof": request.asof.isoformat(), "feed": request.feed, "adjustment": request.adjustment,
                            "calendar_version": self.calendar.version,
                            "security_mapping": {"valid_from": request.security.valid_from.isoformat(),
                                                 "valid_to": request.security.valid_to.isoformat() if request.security.valid_to else None,
                                                 "identity_source": request.security.identity_source,
                                                 "exchange": request.security.exchange,
                                                 "asset_type": request.security.asset_type,
                                                 "currency": request.security.currency}}
        provenance = DataProvenance(
            provider=self.name, feed=request.feed, adjustment=request.adjustment,
            asof=request.asof.isoformat(), retrieved_at=response.retrieved_at.isoformat(),
            source_sha256=response.sha256,
            request_sha256=hashlib.sha256(json.dumps(request_identity, sort_keys=True).encode()).hexdigest(),
            limitations=LIMITATIONS + (("public_demo",) if self._api_key == "demo" else ()),
        )
        frame = pd.DataFrame(rows)
        frame.attrs["research_only"] = True
        frame.attrs["data_provenance"] = provenance.__dict__
        frame.attrs["missing_sessions"] = missing
        return PriceBatch(frame, provenance, tuple(missing))

    def fetch_corporate_actions(self, request: PriceRequest) -> tuple[dict[str, Any], ...]:
        raise DataCapabilityError("CORPORATE_ACTIONS_UNAVAILABLE", "Free raw daily feed has no verified event ledger.",
                                  next_action="configure_licensed_event_provider")

    def fetch_historical_universe(self, universe: str, session: date) -> tuple[SecurityMapping, ...]:
        raise DataCapabilityError("HISTORICAL_UNIVERSE_UNAVAILABLE", "A fixed cohort is not historical membership.",
                                  next_action="configure_licensed_security_master")
