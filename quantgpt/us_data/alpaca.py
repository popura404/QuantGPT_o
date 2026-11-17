"""Alpaca historical SIP daily bars with explicit source, identity and pagination."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pandas as pd

from .calendar import NewYorkCalendar, TradingSession
from .contracts import DataCapabilityError, DataProvenance, PriceBatch, PriceRequest, SecurityMapping
from .http import JSONClient

NY = ZoneInfo("America/New_York")
LIMITATIONS = (
    "corporate_actions_unavailable", "historical_membership_unavailable",
    "delisting_terminal_values_unavailable", "security_master_caller_supplied", "upstream_vintages_unknown",
)
_COLUMNS = ("security_id", "stock_code", "ticker", "trade_date", "session", "open", "high", "low", "close",
            "volume", "available_at", "currency", "adjustment")


def daily_available_at(session: date) -> datetime:
    """SIP daily volume includes extended hours, so wait for the complete New York day."""
    return datetime.combine(session + timedelta(days=1), time(0, 15), NY)


class AlpacaDailyProvider:
    name = "alpaca_sip"

    def __init__(self, api_key: str | None = None, secret_key: str | None = None, *, http: JSONClient | None = None,
                 calendar: NewYorkCalendar | None = None, now: Callable[[], datetime] | None = None, max_pages: int = 100):
        self._api_key = os.environ.get("APCA_API_KEY_ID", "") if api_key is None else api_key
        self._secret_key = os.environ.get("APCA_API_SECRET_KEY", "") if secret_key is None else secret_key
        self.http = http or JSONClient()
        self.calendar = calendar or NewYorkCalendar()
        self.now = now or (lambda: datetime.now(timezone.utc))
        if isinstance(max_pages, bool) or not isinstance(max_pages, int) or max_pages < 1:
            raise ValueError("max_pages must be a positive integer")
        self.max_pages = max_pages

    def capabilities(self) -> dict[str, Any]:
        return {"provider": self.name, "market": "us", "status": "research_only", "ohlcv": True,
                "raw_prices": True, "corporate_actions": False, "historical_universe": False,
                "pagination": "next_page_token", "feed": "sip", "currency": "USD",
                "credentials_configured": bool(self._api_key and self._secret_key),
                "calendar_version": self.calendar.version, "limitations": list(LIMITATIONS)}

    def fetch_prices(self, request: PriceRequest) -> PriceBatch:
        request.validate()
        if request.feed != "sip":
            raise DataCapabilityError("FEED_UNSUPPORTED", "Alpaca research requests require the SIP feed.")
        if not self._api_key or not self._secret_key:
            raise DataCapabilityError("PROVIDER_CREDENTIALS_MISSING", "Alpaca key ID and secret key are required.",
                                      next_action="configure_alpaca_credentials")
        ticker = request.security.ticker.upper()
        if not re.fullmatch(r"[A-Z0-9.-]{1,20}", ticker):
            raise ValueError("unsupported US ticker syntax")
        now = self.now()
        if now.tzinfo is None:
            raise ValueError("provider clock must be timezone-aware")
        cutoff = min(now, request.asof)
        sessions = self.calendar.sessions(request.start, request.end)
        eligible = {row.session.isoformat(): row for row in sessions if daily_available_at(row.session) <= cutoff}
        if not eligible:
            raise DataCapabilityError("PRICE_COVERAGE_INCOMPLETE", "No complete daily session is available at the cutoff.",
                                      next_action="choose_completed_sessions")
        query_end = max(daily_available_at(row.session) for row in eligible.values()) - timedelta(
            minutes=15, microseconds=1,
        )
        params = {
            "symbols": ticker, "timeframe": "1Day", "feed": "sip", "adjustment": "raw", "asof": "-",
            "start": datetime.combine(request.start, time.min, NY).isoformat(),
            "end": query_end.isoformat(),
            "limit": "10000", "sort": "asc", "currency": "USD",
        }
        headers = {"APCA-API-KEY-ID": self._api_key, "APCA-API-SECRET-KEY": self._secret_key}
        seen_tokens: set[str] = set()
        rows: dict[str, dict[str, Any]] = {}
        page_hashes = []
        retrieved_at = now
        for _ in range(self.max_pages):
            response = self.http.get("https://data.alpaca.markets/v2/stocks/bars", params=params, headers=headers)
            page_hashes.append(response.sha256)
            retrieved_at = response.retrieved_at
            payload = response.data
            if "bars" not in payload or not isinstance(payload["bars"], dict):
                raise DataCapabilityError("PROVIDER_SCHEMA_ERROR", "Alpaca response is missing its symbol-to-bars object.")
            if set(payload["bars"]) - {ticker}:
                raise DataCapabilityError("PROVIDER_IDENTITY_MISMATCH", "Alpaca returned an unrequested symbol.")
            bars = payload["bars"].get(ticker, [])
            if not isinstance(bars, list):
                raise DataCapabilityError("PROVIDER_SCHEMA_ERROR", "Alpaca symbol bars must be a list.")
            for bar in bars:
                self.http.cancel_check()
                row = self._daily_row(bar, request.security, eligible)
                label = row["session"]
                if label in rows:
                    raise DataCapabilityError("PROVIDER_DUPLICATE_SESSION", "Alpaca returned a duplicate daily session.")
                rows[label] = row
            token = payload.get("next_page_token")
            if token is None:
                break
            if not isinstance(token, str) or not token or token in seen_tokens:
                raise DataCapabilityError("PROVIDER_PAGE_REJECTED", "Alpaca returned an invalid or repeated page token.")
            seen_tokens.add(token)
            params = {**params, "page_token": token}
        else:
            raise DataCapabilityError("PAGINATION_BUDGET_EXCEEDED", "Alpaca history exceeds the configured page budget.",
                                      next_action="increase_page_budget_or_split_date_range")

        # Every requested session is reported, including a date whose full bar
        # has not yet become available. Consumers can enforce a complete window.
        missing = tuple(session.session.isoformat() for session in sessions if session.session.isoformat() not in rows)
        mapping = request.security
        request_identity = {
            "security_id": mapping.security_id, "ticker": ticker, "valid_from": mapping.valid_from.isoformat(),
            "valid_to": mapping.valid_to.isoformat() if mapping.valid_to else None,
            "identity_source": mapping.identity_source, "exchange": mapping.exchange,
            "asset_type": mapping.asset_type, "currency": mapping.currency,
            "start": request.start.isoformat(), "end": request.end.isoformat(), "asof": request.asof.isoformat(),
            "feed": "sip", "adjustment": "raw", "symbol_mapping": "disabled",
            "query_end": params["end"], "calendar_version": self.calendar.version,
        }
        provenance = DataProvenance(
            provider=self.name, feed="sip", adjustment="raw", asof=request.asof.isoformat(),
            retrieved_at=retrieved_at.isoformat(),
            source_sha256=hashlib.sha256(json.dumps(page_hashes).encode()).hexdigest(),
            request_sha256=hashlib.sha256(json.dumps(request_identity, sort_keys=True).encode()).hexdigest(),
            limitations=LIMITATIONS,
        )
        frame = pd.DataFrame([rows[label] for label in sorted(rows)], columns=list(_COLUMNS))
        frame.attrs.update(research_only=True, data_provenance=provenance.__dict__, missing_sessions=list(missing))
        return PriceBatch(frame, provenance, missing)

    @staticmethod
    def _daily_row(raw: Any, security: SecurityMapping, sessions: dict[str, TradingSession]) -> dict[str, Any]:
        try:
            if not isinstance(raw, dict) or not isinstance(raw.get("t"), str):
                raise ValueError
            stamp = datetime.fromisoformat(raw["t"])
            if stamp.tzinfo is None:
                raise ValueError
            local = stamp.astimezone(NY)
            if local.time() != time.min:
                raise ValueError
            label = local.date().isoformat()
            values = [raw[key] for key in ("o", "h", "l", "c", "v")]
            if any(isinstance(value, bool) for value in values):
                raise ValueError
            o, high, low, close, volume = (float(value) for value in values)
        except (ValueError, TypeError, KeyError, OverflowError):
            raise DataCapabilityError("PROVIDER_SCHEMA_ERROR", "Alpaca daily timestamp or OHLCV fields are malformed.") from None
        if label not in sessions:
            raise DataCapabilityError("PROVIDER_SESSION_MISMATCH", "Alpaca returned a session outside the eligible window.")
        if (not all(math.isfinite(value) for value in (o, high, low, close, volume))
                or min(o, high, low, close) <= 0 or volume < 0
                or not low <= min(o, close) <= max(o, close) <= high):
            raise DataCapabilityError("INVALID_OHLCV", "Daily OHLCV is nonfinite, nonpositive or has inconsistent bounds.")
        return {"security_id": security.security_id, "stock_code": security.security_id, "ticker": security.ticker.upper(),
                "trade_date": pd.Timestamp(label), "session": label, "open": o, "high": high, "low": low, "close": close,
                "volume": volume, "available_at": daily_available_at(sessions[label].session).isoformat(),
                "currency": "USD", "adjustment": "raw"}

    def fetch_corporate_actions(self, request: PriceRequest) -> tuple[dict[str, Any], ...]:
        raise DataCapabilityError("CORPORATE_ACTIONS_UNAVAILABLE", "Daily bars do not establish a verified event ledger.",
                                  next_action="register_corporate_action_evidence")

    def fetch_historical_universe(self, universe: str, session: date) -> tuple[SecurityMapping, ...]:
        raise DataCapabilityError("HISTORICAL_UNIVERSE_UNAVAILABLE", "Historical membership requires a registered source.",
                                  next_action="register_historical_universe_evidence")
