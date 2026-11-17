"""Join SEC issuer facts to daily prices using each row's historical decision time."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, cast

import pandas as pd

from .calendar import NewYorkCalendar
from .contracts import DataCapabilityError
from .sec import FIELD_CONTRACTS, FactVintage, SECCompanyFactsProvider, select_asof

DEFAULT_FIELDS = ("total_assets", "total_liabilities")
QUARTER_FIELDS = frozenset({"net_income", "diluted_eps_period"})


def _decision_times(panel: pd.DataFrame, calendar: NewYorkCalendar) -> list[datetime]:
    date_column = "session" if "session" in panel else "trade_date"
    if date_column not in panel:
        raise DataCapabilityError("SEC_DECISION_TIME_REQUIRED", "Daily fundamentals require session or trade_date.")
    try:
        dates = []
        for value in panel[date_column]:
            timestamp = pd.Timestamp(value)
            if pd.isna(timestamp):
                raise ValueError("Missing session date")
            dates.append(cast(date, timestamp.date()))
        sessions = calendar.sessions(min(dates), max(dates) + timedelta(days=14))
        closes = {item.session: item.closes_at for item in sessions}
        next_opens = {current.session: following.opens_at for current, following in zip(sessions, sessions[1:])}
        result = []
        for position, day in enumerate(dates):
            if day not in closes:
                raise ValueError("Non-trading session")
            value = pd.Timestamp(panel.iloc[position]["decision_at"]) if "decision_at" in panel else pd.Timestamp(closes[day])
            if pd.isna(value) or value.tzinfo is None or value < closes[day]:
                raise ValueError("Invalid decision timestamp")
            if day not in next_opens or value >= next_opens[day]:
                raise ValueError("Decision timestamp reaches next session")
            result.append(cast(datetime, value.to_pydatetime()).astimezone(timezone.utc))
        return result
    except (TypeError, ValueError, KeyError, OverflowError):
        raise DataCapabilityError("SEC_DECISION_TIME_REQUIRED", "Use exchange sessions and timezone-aware decision_at between their close and the next session open.") from None


def _latest_fact(facts: tuple[FactVintage, ...], asof: datetime) -> FactVintage | None:
    eligible = select_asof(facts, asof)
    if not eligible:
        return None
    latest_end = max(fact.period_end for fact in eligible)
    latest = [fact for fact in eligible if fact.period_end == latest_end]
    if len(latest) != 1:
        raise DataCapabilityError("AMBIGUOUS_FINANCIAL_PERIOD", "Multiple reporting periods end on the latest financial date.")
    return latest[0]


def enrich_sec_fundamentals(panel: pd.DataFrame, issuer_ciks: dict[str, str], *,
                            provider: SECCompanyFactsProvider, fields: tuple[str, ...] = DEFAULT_FIELDS,
                            calendar: NewYorkCalendar | None = None) -> pd.DataFrame:
    """Attach exact issuer fields and their SEC accessions, preserving missing pre-filing observations.

    net_income and diluted_eps_period select only explicit 60–120 day quarters.
    The other fields select instantaneous observations. No annual/YTD subtraction,
    share-class allocation or split adjustment is inferred.
    """
    if not fields or panel.empty:
        return panel.copy()
    if len(fields) != len(set(fields)) or any(field not in FIELD_CONTRACTS for field in fields):
        raise DataCapabilityError("FIELD_NOT_EQUIVALENT", "Select unique fields from the SEC field contracts.")
    if not panel.columns.is_unique:
        raise DataCapabilityError("PROVIDER_SCHEMA_ERROR", "Daily panel column names must be unique.")
    if "security_id" not in panel or cast(pd.Series, panel["security_id"]).isna().any():
        raise DataCapabilityError("SECURITY_MAPPING_UNAVAILABLE", "SEC enrichment requires stable security_id values.")
    securities = tuple(str(item) for item in panel["security_id"].unique())
    if any(not item or item not in issuer_ciks for item in securities):
        raise DataCapabilityError("SEC_ISSUER_MAPPING_REQUIRED", "Supply an explicit issuer CIK for every requested security_id.")
    try:
        mappings = {security: provider.normalize_cik(issuer_ciks[security]) for security in securities}
    except ValueError:
        raise DataCapabilityError("SEC_ISSUER_MAPPING_REQUIRED", "Every issuer CIK must be a positive number with at most ten digits.") from None
    decisions = _decision_times(panel, calendar or NewYorkCalendar())
    issuer_facts: dict[str, dict[str, tuple[FactVintage, ...]]] = {}
    sources = []
    for cik in sorted(set(mappings.values())):
        facts = provider.fetch_facts(cik, fields=fields)
        selected_fields = {}
        for field in fields:
            taxonomy, tag, unit = FIELD_CONTRACTS[field]
            eligible = tuple(fact for fact in facts if (fact.taxonomy, fact.tag, fact.unit) == (taxonomy, tag, unit)
                             and ((fact.period_start is not None and 60 <= (fact.period_end - fact.period_start).days <= 120)
                                  if field in QUARTER_FIELDS else fact.period_start is None))
            if not eligible:
                raise DataCapabilityError("FIELD_PERIOD_UNAVAILABLE", f"SEC field {field} has no observations in its required reporting period.")
            selected_fields[field] = eligible
        issuer_facts[cik] = selected_fields
        sources.append({"issuer_cik": cik, "provider": provider.name,
                        "source_ref": f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
                        "source_sha256": facts[0].source_sha256, "retrieved_at": facts[0].retrieved_at.isoformat()})
    output = panel.copy()
    row_securities = tuple(str(item) for item in panel["security_id"])
    for field in fields:
        releases = {cik: sorted({fact.available_at for fact in observations[field]})
                    for cik, observations in issuer_facts.items()}
        selected_versions: dict[tuple[str, datetime], FactVintage | None] = {}
        values: list[dict[str, Any]] = []
        for security, decision in zip(row_securities, decisions, strict=True):
            provider.http.cancel_check()
            cik = mappings[security]
            position = bisect_right(releases[cik], decision)
            fact = None
            if position:
                key = (cik, releases[cik][position - 1])
                if key not in selected_versions:
                    selected_versions[key] = _latest_fact(issuer_facts[cik][field], key[1])
                fact = selected_versions[key]
            row = asdict(fact) if fact else {}
            values.append({field: row.get("value"), f"{field}_period_start": row.get("period_start"),
                           f"{field}_period_end": row.get("period_end"), f"{field}_filed": row.get("filed"),
                           f"{field}_available_at": row.get("available_at"), f"{field}_revision_id": row.get("accession")})
        for column in values[0]:
            output[column] = [row[column] for row in values]
        output[field] = output[field].astype(float)
    output.attrs["fundamental_provenance"] = {
        "provider": provider.name, "fields": list(fields), "issuer_ciks": mappings, "sources": sources,
        "field_contracts": {field: {"taxonomy": FIELD_CONTRACTS[field][0], "tag": FIELD_CONTRACTS[field][1],
                                    "unit": FIELD_CONTRACTS[field][2], "period": "quarter" if field in QUARTER_FIELDS else "instant",
                                    "revision_id": "SEC accession"} for field in fields},
        "missing_observations": {field: int(cast(pd.Series, output[field]).isna().sum()) for field in fields},
        "availability_policy": "filing_date_plus_one_calendar_day_ny_midnight",
        "limitations": provider.capabilities()["limitations"],
    }
    return output
