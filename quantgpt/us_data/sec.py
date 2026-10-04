"""SEC company facts with explicit units, periods, accession vintages and as-of selection."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .contracts import DataCapabilityError
from .http import JSONClient

NY = ZoneInfo("America/New_York")
FIELD_CONTRACTS = {
    "net_income": ("us-gaap", "NetIncomeLoss", "USD"),
    "total_assets": ("us-gaap", "Assets", "USD"),
    "total_liabilities": ("us-gaap", "Liabilities", "USD"),
    "reported_common_shares": ("dei", "EntityCommonStockSharesOutstanding", "shares"),
    "diluted_eps_period": ("us-gaap", "EarningsPerShareDiluted", "USD/shares"),
}


@dataclass(frozen=True)
class FactVintage:
    issuer_cik: str
    taxonomy: str
    tag: str
    unit: str
    value: float
    period_start: date | None
    period_end: date
    filed: date
    available_at: datetime
    accession: str
    form: str
    fiscal_year: int | None
    fiscal_period: str
    retrieved_at: datetime
    source_sha256: str
    availability_policy: str = "filing_date_plus_one_calendar_day_ny_midnight"
    version: str = "sec_companyfacts/v1"


class SECCompanyFactsProvider:
    name = "sec_companyfacts"

    def __init__(self, user_agent: str, *, http: JSONClient | None = None):
        if not user_agent.strip() or "@" not in user_agent or "\n" in user_agent or "\r" in user_agent:
            raise DataCapabilityError("SEC_CONTACT_REQUIRED", "Provide an identifying application and real contact User-Agent.",
                                      next_action="configure_sec_user_agent")
        self._headers = {"User-Agent": user_agent, "Accept": "application/json"}
        self.http = http or JSONClient()

    @staticmethod
    def normalize_cik(cik: str | int) -> str:
        text = str(cik)
        if not re.fullmatch(r"\d{1,10}", text) or int(text) <= 0:
            raise ValueError("CIK must be a positive issuer number of at most 10 digits")
        return text.zfill(10)

    def capabilities(self) -> dict[str, Any]:
        return {"provider": self.name, "status": "research_only", "api_key_required": False,
                "issuer_level": True, "companyfacts_pagination": "not_needed_single_document",
                "submissions_pagination": "files_array", "financial_vintages": True,
                "availability_policy": "filed_date_plus_one_calendar_day_ny_midnight",
                "share_split_adjustment": "unknown", "historical_industry": False,
                "limitations": ["custom_taxonomies_not_in_aggregate", "entity_level_not_security_level",
                                "date_only_publication_is_conservative", "historical_feed_vintage_unknown"]}

    def fetch_facts(self, cik: str | int, *, fields: tuple[str, ...] | None = None) -> tuple[FactVintage, ...]:
        normalized = self.normalize_cik(cik)
        wanted = fields or tuple(FIELD_CONTRACTS)
        if any(field not in FIELD_CONTRACTS for field in wanted):
            raise DataCapabilityError("FIELD_NOT_EQUIVALENT", "Requested field has no exact SEC taxonomy/unit mapping.")
        response = self.http.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{normalized}.json", headers=self._headers)
        if self.normalize_cik(response.data.get("cik", "")) != normalized:
            raise DataCapabilityError("PROVIDER_IDENTITY_MISMATCH", "SEC response CIK differs from the request.")
        facts = []
        for field_name in wanted:
            taxonomy, tag, unit = FIELD_CONTRACTS[field_name]
            entries = response.data.get("facts", {}).get(taxonomy, {}).get(tag, {}).get("units", {}).get(unit)
            if not entries:
                raise DataCapabilityError("FIELD_UNAVAILABLE", f"Exact field {field_name} ({taxonomy}:{tag}, {unit}) is absent.")
            for entry in entries:
                self.http.cancel_check()
                try:
                    filed = date.fromisoformat(entry["filed"])
                    value = float(entry["val"])
                    end = date.fromisoformat(entry["end"])
                    start = date.fromisoformat(entry["start"]) if entry.get("start") else None
                    accession = str(entry["accn"])
                    form = str(entry["form"])
                except (KeyError, ValueError, TypeError) as exc:
                    raise DataCapabilityError("PROVIDER_SCHEMA_ERROR", "SEC fact is missing a required vintage/period field.") from exc
                if not math.isfinite(value) or (start is not None and start > end) or end > filed:
                    raise DataCapabilityError("INVALID_FINANCIAL_FACT", "SEC fact has an invalid numeric value or period.")
                if not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession):
                    raise DataCapabilityError("INVALID_FINANCIAL_FACT", "SEC accession is malformed.")
                # Companyfacts gives a filing date, not a reliable intraday publication timestamp.
                # Waiting until the next calendar date prevents same-day/after-hours lookahead.
                available = datetime.combine(filed + timedelta(days=1), time.min, NY).astimezone(timezone.utc)
                facts.append(FactVintage(
                    issuer_cik=normalized, taxonomy=taxonomy, tag=tag, unit=unit, value=value,
                    period_start=start, period_end=end, filed=filed, available_at=available,
                    accession=accession, form=form, fiscal_year=entry.get("fy"), fiscal_period=str(entry.get("fp", "")),
                    retrieved_at=response.retrieved_at, source_sha256=response.sha256,
                ))
        return tuple(facts)

    def fetch_submissions(self, cik: str | int, *, max_pages: int = 20) -> tuple[dict[str, Any], ...]:
        """Read all declared filing-history pages, never follow arbitrary returned URLs."""
        if max_pages < 1:
            raise ValueError("max_pages must be positive")
        normalized = self.normalize_cik(cik)
        main = self.http.get(f"https://data.sec.gov/submissions/CIK{normalized}.json", headers=self._headers)
        if self.normalize_cik(main.data.get("cik", "")) != normalized:
            raise DataCapabilityError("PROVIDER_IDENTITY_MISMATCH", "SEC submissions CIK differs from the request.")
        files = main.data.get("filings", {}).get("files", [])
        if len(files) + 1 > max_pages:
            raise DataCapabilityError("PAGINATION_BUDGET_EXCEEDED", "SEC filing history exceeds the configured page budget.",
                                      next_action="increase_explicit_page_budget")
        pages = [main.data.get("filings", {}).get("recent", {})]
        seen = set()
        for entry in files:
            name = entry.get("name", "")
            if not re.fullmatch(rf"CIK{normalized}-submissions-\d+\.json", name) or name in seen:
                raise DataCapabilityError("PROVIDER_PAGE_REJECTED", "SEC returned an unsafe or duplicate page reference.")
            seen.add(name)
            response = self.http.get(f"https://data.sec.gov/submissions/{name}", headers=self._headers)
            pages.append(response.data)
        return tuple(pages)


def select_asof(facts: tuple[FactVintage, ...], asof: datetime) -> tuple[FactVintage, ...]:
    """Select a vintage separately for each exact taxonomy/unit/start/end contract."""
    if asof.tzinfo is None:
        raise ValueError("asof must be timezone-aware")
    selected: dict[tuple[str, str, str, str, date | None, date], FactVintage] = {}
    for fact in facts:
        if fact.available_at > asof:
            continue
        key = (fact.issuer_cik, fact.taxonomy, fact.tag, fact.unit, fact.period_start, fact.period_end)
        previous = selected.get(key)
        if previous is None or (fact.available_at, fact.accession) > (previous.available_at, previous.accession):
            selected[key] = fact
        elif (fact.available_at, fact.accession) == (previous.available_at, previous.accession) and fact.value != previous.value:
            raise DataCapabilityError("AMBIGUOUS_FINANCIAL_FACT", "One accession has conflicting values for the same period/unit.")
    return tuple(sorted(selected.values(), key=lambda fact: (fact.tag, fact.period_end, fact.period_start or date.min)))


def ttm_from_quarters(facts: tuple[FactVintage, ...], *, asof: datetime) -> float:
    """Sum four explicit contiguous quarters; do not add cumulative YTD and annual values."""
    values = sorted(select_asof(facts, asof), key=lambda fact: fact.period_end)
    if len(values) != 4 or len({(fact.issuer_cik, fact.taxonomy, fact.tag, fact.unit) for fact in values}) != 1:
        raise DataCapabilityError("TTM_PERIODS_REQUIRED", "TTM needs exactly four quarters of one issuer/tag/unit.")
    for index, fact in enumerate(values):
        if fact.period_start is None or not 60 <= (fact.period_end - fact.period_start).days <= 120:
            raise DataCapabilityError("TTM_PERIODS_REQUIRED", "Annual or cumulative YTD observations cannot substitute for a quarter.")
        if index and fact.period_start != values[index - 1].period_end + timedelta(days=1):
            raise DataCapabilityError("TTM_PERIODS_REQUIRED", "Quarter periods must be contiguous and non-overlapping.")
    return sum(fact.value for fact in values)
