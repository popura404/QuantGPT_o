"""Integrity-checked local response replay for the two primary US data sources."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import httpx
from filelock import FileLock, Timeout

from .contracts import DataCapabilityError
from .http import JSONClient, JSONResponse

_VERSION = "us_response_cache/v1"
_ALPACA_PARAMS = {"symbols", "timeframe", "feed", "adjustment", "asof", "start", "end", "limit", "sort", "currency",
                  "page_token"}
_PRIVATE_HEADERS = {"apca-api-key-id", "apca-api-secret-key", "authorization", "user-agent"}


def _identity(url: str, params: dict[str, str] | None, headers: dict[str, str] | None) -> str:
    # Match httpx's URL/params merge semantics, then sort unique query names.
    try:
        original = urlsplit(url)
        parsed = urlsplit(str(httpx.Request("GET", url, params=params).url))
    except (httpx.InvalidURL, ValueError):
        raise DataCapabilityError("CACHE_REQUEST_REJECTED", "Provider request URL is invalid.") from None
    query = parse_qsl(parsed.query, keep_blank_values=True)
    names = [name for name, _ in query]
    alpaca = parsed.netloc == "data.alpaca.markets" and parsed.path == "/v2/stocks/bars"
    sec = parsed.netloc == "data.sec.gov" and bool(re.fullmatch(
        r"/(?:api/xbrl/companyfacts/CIK\d{10}|submissions/CIK\d{10}(?:-submissions-\d+)?)\.json", parsed.path,
    ))
    if (parsed.scheme != "https" or parsed.fragment or not (alpaca or sec)
            or len(names) != len(set(names)) or (sec and query)
            or (alpaca and set(names) - _ALPACA_PARAMS)
            or any(name not in _ALPACA_PARAMS for name, _ in parse_qsl(original.query, keep_blank_values=True))):
        raise DataCapabilityError("CACHE_REQUEST_REJECTED", "Only approved credential-free provider queries can be cached.")
    private = [value for name, value in (headers or {}).items() if name.lower() in _PRIVATE_HEADERS and value]
    if any(secret in value for _, value in query for secret in private):
        raise DataCapabilityError("CACHE_REQUEST_REJECTED", "Request credentials must be supplied through headers only.")
    identity = {"scheme": parsed.scheme, "host": parsed.netloc, "path": parsed.path, "query": sorted(query)}
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _invalid() -> DataCapabilityError:
    return DataCapabilityError("CACHE_INVALID", "Cached response failed integrity validation; explicitly refresh the source.",
                               next_action="refresh_provider_response")


def _decode(raw: bytes) -> dict[str, Any]:
    def reject_constant(value: str) -> None:
        raise ValueError("Nonfinite JSON value")

    data = json.loads(raw, parse_constant=reject_constant)
    if not isinstance(data, dict):
        raise ValueError("Expected an object")
    return data


class CachedJSONClient(JSONClient):
    """Replay exact response bytes; refresh is explicit and failed refreshes preserve prior data."""

    def __init__(self, root: Path, *, refresh: bool = False, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.root = Path(root)
        self.refresh = refresh

    def get(self, url: str, *, params: dict[str, str] | None = None,
            headers: dict[str, str] | None = None) -> JSONResponse:
        self.cancel_check()
        key = _identity(url, params, headers)
        path = self.root / f"{key}.json"
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            with FileLock(str(self.root / f".{key}.lock"), timeout=30):
                self.cancel_check()
                if path.exists() and not self.refresh:
                    return self._read(path, key)
                response = super().get(url, params=params, headers=headers)
                self.cancel_check()
                raw = response.raw
                if raw is None or hashlib.sha256(raw).hexdigest() != response.sha256:
                    raise _invalid()
                # Reject credential echoes even when JSON escaping hides the plain
                # value in the response bytes. Authentication headers are never stored.
                public = json.dumps(response.data, ensure_ascii=False)
                if any(value and (value in public or value.encode() in raw)
                       for name, value in (headers or {}).items() if name.lower() in _PRIVATE_HEADERS):
                    raise DataCapabilityError("CACHE_SECRET_REJECTED", "Provider response echoed private request headers.")
                entry = {"schema_version": _VERSION, "request_sha256": key, "source_sha256": response.sha256,
                         "retrieved_at": response.retrieved_at.isoformat(),
                         "body_base64": base64.b64encode(raw).decode("ascii")}
                # Validate through the same path used during replay before publication.
                self._response(entry, key)
                temporary: str | None = None
                try:
                    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.root, prefix=".response-",
                                                     suffix=".tmp", delete=False) as stream:
                        temporary = stream.name
                        json.dump(entry, stream, sort_keys=True)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, path)
                finally:
                    if temporary is not None:
                        Path(temporary).unlink(missing_ok=True)
                return response
        except Timeout:
            raise DataCapabilityError("CACHE_BUSY", "Another process is preparing this response.", retryable=True,
                                      next_action="retry_read_later") from None
        except InterruptedError:
            raise
        except OSError:
            raise DataCapabilityError("CACHE_IO_ERROR", "Response cache could not be read or written.",
                                      next_action="check_cache_directory") from None

    @classmethod
    def _read(cls, path: Path, key: str) -> JSONResponse:
        try:
            entry = json.loads(path.read_bytes())
        except (ValueError, UnicodeError):
            raise _invalid() from None
        return cls._response(entry, key)

    @staticmethod
    def _response(entry: Any, key: str) -> JSONResponse:
        try:
            if not isinstance(entry, dict) or entry["schema_version"] != _VERSION or entry["request_sha256"] != key:
                raise ValueError
            raw = base64.b64decode(entry["body_base64"], validate=True)
            digest = hashlib.sha256(raw).hexdigest()
            if digest != entry["source_sha256"]:
                raise ValueError
            retrieved_at = datetime.fromisoformat(entry["retrieved_at"])
            if retrieved_at.tzinfo is None:
                raise ValueError
            data = _decode(raw)
        except (ValueError, TypeError, KeyError, UnicodeError, binascii.Error):
            raise _invalid() from None
        return JSONResponse(data, digest, retrieved_at, raw)
