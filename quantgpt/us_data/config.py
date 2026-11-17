"""Explicit security cohorts shared by the US adapter and snapshot preparation."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from .contracts import SecurityMapping
from .sec import SECCompanyFactsProvider


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("US cohort JSON must not repeat object keys")
        result[key] = value
    return result


def _iso_date(value: Any, field: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a YYYY-MM-DD date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{field} must be a YYYY-MM-DD date") from None
    if parsed.isoformat() != value:
        raise ValueError(f"{field} must be a YYYY-MM-DD date")
    return parsed


def load_security_cohort(path: str | Path) -> tuple[str, tuple[SecurityMapping, ...], dict[str, str]]:
    document = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    if not isinstance(document, dict) or set(document) != {"universe_id", "securities"}:
        raise ValueError("Expected universe_id and securities in the US cohort file")
    universe = document["universe_id"]
    entries = document["securities"]
    if not isinstance(universe, str) or not universe.strip() or not isinstance(entries, list) or not entries:
        raise ValueError("A named, nonempty US security cohort is required")
    securities, ciks = [], {}
    for entry in entries:
        required = {"security_id", "ticker", "valid_from"}
        optional = {"valid_to", "issuer_cik", "asset_type", "currency", "exchange", "identity_source"}
        if not isinstance(entry, dict) or not required <= set(entry) or set(entry) - required - optional:
            raise ValueError("Each security requires security_id, ticker, valid_from and supported mapping fields only")
        values = dict(entry)
        cik = values.pop("issuer_cik", None)
        for field in ("security_id", "ticker", "asset_type", "currency", "exchange", "identity_source"):
            if field in values and (not isinstance(values[field], str) or not values[field].strip()
                                    or values[field].strip() != values[field]):
                raise ValueError(f"{field} must be a nonempty string without surrounding whitespace")
        values["valid_from"] = _iso_date(values["valid_from"], "valid_from")
        values["valid_to"] = _iso_date(values["valid_to"], "valid_to") if values.get("valid_to") is not None else None
        if values["valid_to"] is not None and values["valid_to"] < values["valid_from"]:
            raise ValueError("valid_to must not precede valid_from")
        security = SecurityMapping(**values)
        security.validate_range(security.valid_from, security.valid_to or security.valid_from)
        securities.append(security)
        if cik is not None:
            ciks[security.security_id] = SECCompanyFactsProvider.normalize_cik(cik)
    if len({item.security_id for item in securities}) != len(securities):
        raise ValueError("Security IDs must be unique within a fixed cohort")
    if len({item.ticker.upper() for item in securities}) != len(securities):
        raise ValueError("Ticker mappings must be unique within a fixed cohort")
    return universe.strip(), tuple(securities), ciks
