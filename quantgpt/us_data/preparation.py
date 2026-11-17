"""Prepare immutable US input panels; no factor evaluation or experiment submission."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

from quantgpt.data_snapshots import freeze_market_frame
from quantgpt.research.contracts import FieldContract, ResearchScope
from quantgpt.research.market_inputs import check_signal_availability
from quantgpt.strategy.us_adapter import USResearchAdapter

from .alpaca import AlpacaDailyProvider, daily_available_at
from .config import load_security_cohort
from .contracts import DataCapabilityError
from .fundamentals import DEFAULT_FIELDS, QUARTER_FIELDS
from .sec import FIELD_CONTRACTS, SECCompanyFactsProvider


def field_catalog(fields: tuple[str, ...]) -> tuple[FieldContract, ...]:
    prices = tuple(FieldContract(
        name=name, dtype="float64", unit="shares" if name == "volume" else "USD/share",
        currency=None if name == "volume" else "USD", economic_meaning=f"Alpaca SIP raw daily {name}",
        available_at_rule="complete NY natural day plus 15 minutes; next day 00:15 America/New_York",
        adjustment="raw", period="session",
    ) for name in ("open", "high", "low", "close", "volume"))
    fundamentals = tuple(FieldContract(
        name=name, dtype="float64", unit=FIELD_CONTRACTS[name][2],
        currency=None if FIELD_CONTRACTS[name][2] == "shares" else "USD",
        economic_meaning=f"SEC {FIELD_CONTRACTS[name][0]}:{FIELD_CONTRACTS[name][1]}; issuer consolidated filing",
        available_at_rule="SEC filing date plus one calendar day at New York midnight; accession as of decision_at",
        period="quarter" if name in QUARTER_FIELDS else "instant", requires_vintage=True,
    ) for name in fields)
    return prices + fundamentals


def prepare_us_snapshot(securities_file: str | Path, start: date, end: date, root: str | Path, *,
                        prices: AlpacaDailyProvider, fundamentals: SECCompanyFactsProvider | None = None,
                        fields: tuple[str, ...] = DEFAULT_FIELDS) -> dict:
    """Fetch a declared cohort once and publish a snapshot for the existing project import tools."""
    if start > end:
        raise ValueError("start must not exceed end")
    universe, securities, ciks = load_security_cohort(securities_file)
    for security in securities:
        security.validate_range(start, end)
    if fields and (fundamentals is None or any(item.security_id not in ciks for item in securities)):
        raise DataCapabilityError("SEC_CIK_REQUIRED", "Requested fundamentals need a SEC provider and issuer_cik for every security.")
    if len(set(fields)) != len(fields) or any(name not in FIELD_CONTRACTS for name in fields):
        raise DataCapabilityError("SEC_FIELD_UNSUPPORTED", "Select unique fields from the SEC field catalog.")
    adapter = USResearchAdapter(prices, {universe: securities}, issuer_ciks=ciks,
                                fundamental_fields=fields, fundamentals=fundamentals)
    # A stable cutoff makes identical cached inputs reproducible across invocations.
    # It is an availability cutoff, not a claim about historical vendor revisions.
    panel, codes = adapter.fetch_factor_market_data(universe, start.isoformat(), end.isoformat(),
                                                   asof=daily_available_at(end))
    if panel.empty:
        raise DataCapabilityError("PRICE_COVERAGE_INCOMPLETE", "No daily prices available for the requested cohort.")
    if fields:
        coverage = panel.groupby("security_id")[list(fields)].count().reindex(
            [security.security_id for security in securities], fill_value=0,
        )
        if coverage.eq(0).to_numpy().any():
            raise DataCapabilityError("SEC_FIELD_COVERAGE_INCOMPLETE",
                                      "Every security/SEC field requires at least one disclosed observation in the requested range.",
                                      next_action="change_date_range_or_requested_fields")
    panel = panel.sort_values(["trade_date", "security_id"]).reset_index(drop=True)
    panel["trade_date"] = panel["trade_date"].astype("datetime64[ns]")
    panel.attrs["adjustment"] = "raw"
    catalog = field_catalog(fields)
    availability = check_signal_availability(panel, catalog, calendar_id="XNYS", timezone="America/New_York")
    identities = [{**item.__dict__, "valid_from": item.valid_from.isoformat(),
                   "valid_to": item.valid_to.isoformat() if item.valid_to else None,
                   "issuer_cik": ciks.get(item.security_id)} for item in securities]
    universe_version = hashlib.sha256(json.dumps(identities, sort_keys=True).encode()).hexdigest()
    scope = ResearchScope(market="us", currency="USD", universe_id=universe, universe_mode="fixed_cohort",
                          universe_version=universe_version, calendar_id="XNYS", calendar_version=prices.calendar.version,
                          timezone="America/New_York", benchmark="none").model_dump(mode="json")
    serialized_catalog = [item.model_dump(mode="json") for item in catalog]
    manifest = freeze_market_frame(
        panel, Path(root) / "imports", vendor="alpaca_sip_sec" if fields else "alpaca_sip",
        query_params={"feed": "sip", "adjustment": "raw", "start": start.isoformat(), "end": end.isoformat(),
                      "securities": identities, "fundamental_fields": list(fields)},
        source_metadata={"synthetic": False, "research_only": True, "scope": scope,
                         "field_catalog": serialized_catalog, "availability_check": availability,
                         "price_availability_policy": "next_calendar_day_0015_America_New_York"},
    )
    return {"manifest_id": manifest["snapshot_id"], "content_sha256": manifest["content_hash"].removeprefix("sha256:"),
            "import_root": str((Path(root) / "imports").resolve()), "source": manifest["vendor"], "feed": "sip",
            "adjustment": "raw", "rows": len(panel), "securities": len(codes),
            "scope": scope, "field_catalog": serialized_catalog, "research_only": True,
            "availability_check": availability, "external_data_acceptance": "not_run",
            "capability_blockers": panel.attrs["capability_blockers"],
            "next_action": "list_research_snapshot_imports_then_register_research_snapshot"}
