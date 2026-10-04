"""Provider-independent contracts retained for future licensed US feeds."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Protocol

import pandas as pd


class DataCapabilityError(ValueError):
    def __init__(self, error_code: str, message: str, *, retryable: bool = False, next_action: str = "check_capabilities"):
        super().__init__(message)
        self.error_code = error_code
        self.retryable = retryable
        self.next_action = next_action

    def to_dict(self) -> dict[str, Any]:
        return {"error_code": self.error_code, "message": str(self),
                "retryable": self.retryable, "next_action": self.next_action}


@dataclass(frozen=True)
class SecurityMapping:
    """A caller-verified security identity; CIK identifies an issuer, not a share class."""

    security_id: str
    ticker: str
    valid_from: date
    valid_to: date | None = None
    asset_type: str = "common_stock"
    currency: str = "USD"
    exchange: str = "XNYS"
    identity_source: str = "caller_supplied_unverified"

    def validate_range(self, start: date, end: date) -> None:
        if not self.security_id or start < self.valid_from or (self.valid_to and end > self.valid_to):
            raise DataCapabilityError("SECURITY_MAPPING_UNAVAILABLE", "Ticker mapping does not cover the requested interval.")
        if self.asset_type != "common_stock" or self.currency != "USD":
            raise DataCapabilityError("ASSET_SCOPE_UNSUPPORTED", "The first US research scope is USD common stock.")


@dataclass(frozen=True)
class PriceRequest:
    security: SecurityMapping
    start: date
    end: date
    asof: datetime
    adjustment: str = "raw"
    feed: str = "TIME_SERIES_DAILY:compact"

    def validate(self) -> None:
        if self.start > self.end:
            raise ValueError("start must not follow end")
        if self.asof.tzinfo is None:
            raise ValueError("asof must be timezone-aware")
        self.security.validate_range(self.start, self.end)
        if self.adjustment != "raw" or self.feed != "TIME_SERIES_DAILY:compact":
            raise DataCapabilityError("FEED_UNSUPPORTED", "The free adapter supports raw daily compact data only.")


@dataclass(frozen=True)
class DataProvenance:
    provider: str
    feed: str
    adjustment: str
    asof: str
    retrieved_at: str
    source_sha256: str
    request_sha256: str
    schema_version: str = "us_research_data/v1"
    upstream_version: str = "unknown"
    status: str = "research_only"
    limitations: tuple[str, ...] = ()


@dataclass
class PriceBatch:
    prices: pd.DataFrame
    provenance: DataProvenance
    missing_sessions: tuple[str, ...] = ()
    next_cursor: str | None = None
    corporate_actions: tuple[dict[str, Any], ...] = field(default_factory=tuple)


class USDataProvider(Protocol):
    """The same narrow boundary can be implemented by a licensed paid provider."""

    name: str

    def capabilities(self) -> dict[str, Any]: ...

    def fetch_prices(self, request: PriceRequest) -> PriceBatch: ...

    def fetch_corporate_actions(self, request: PriceRequest) -> tuple[dict[str, Any], ...]: ...

    def fetch_historical_universe(self, universe: str, session: date) -> tuple[SecurityMapping, ...]: ...
