"""Bounded read-only requests with redacted failures and observable cancellation."""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx

from .contracts import DataCapabilityError


@dataclass(frozen=True)
class JSONResponse:
    data: dict[str, Any]
    sha256: str
    retrieved_at: datetime


class RequestGate:
    """Share one gate per host within this process; distributed callers need a shared quota."""

    def __init__(self, interval: float):
        self.interval = interval
        self._last = float("-inf")
        self._lock = threading.Lock()

    def wait(self, cancel_check: Callable[[], None]) -> None:
        while True:
            cancel_check()
            with self._lock:
                remaining = self.interval - (time.monotonic() - self._last)
                if remaining <= 0:
                    self._last = time.monotonic()
                    return
            time.sleep(min(remaining, 0.1))


GATES = {"www.alphavantage.co": RequestGate(12), "data.sec.gov": RequestGate(0.2)}


class JSONClient:
    def __init__(self, *, client: httpx.Client | None = None, cancel_check: Callable[[], None] | None = None,
                 gate: RequestGate | None = None):
        self.client = client
        self.cancel_check = cancel_check or (lambda: None)
        self.gate = gate

    def get(self, url: str, *, params: dict[str, str] | None = None,
            headers: dict[str, str] | None = None) -> JSONResponse:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.netloc not in GATES:
            raise DataCapabilityError("PROVIDER_URL_REJECTED", "Provider URL is outside the approved endpoint set.")
        (self.gate or GATES[parsed.netloc]).wait(self.cancel_check)
        try:
            if self.client is None:
                with httpx.Client(timeout=20, follow_redirects=False) as client:
                    response = client.get(url, params=params, headers=headers)
            else:
                response = self.client.get(url, params=params, headers=headers, follow_redirects=False, timeout=20)
        except httpx.HTTPError:
            # Exception strings can contain a key-bearing request URL. Never return them.
            raise DataCapabilityError("PROVIDER_NETWORK_ERROR", "Provider request failed.",
                                      retryable=True, next_action="retry_read_later") from None
        self.cancel_check()
        if response.status_code == 429:
            raise DataCapabilityError("PROVIDER_RATE_LIMITED", "Provider rate limit reached; no automatic retry was sent.",
                                      retryable=True, next_action="wait_for_provider_quota")
        if response.status_code != 200:
            raise DataCapabilityError("PROVIDER_HTTP_ERROR", f"Provider returned HTTP {response.status_code}.",
                                      retryable=response.status_code >= 500, next_action="check_provider_access")
        try:
            data = response.json()
        except ValueError as exc:
            raise DataCapabilityError("PROVIDER_SCHEMA_ERROR", "Expected a JSON object from the provider.") from exc
        if not isinstance(data, dict):
            raise DataCapabilityError("PROVIDER_SCHEMA_ERROR", "Expected a JSON object from the provider.")
        return JSONResponse(data, hashlib.sha256(response.content).hexdigest(), datetime.now(timezone.utc))
