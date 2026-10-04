"""Offline US contracts: no API keys, paid requests or real-data performance claims."""

import json
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import pytest

from quantgpt.factor_values import compute_factor_values_payload, validate_factor_values_request
from quantgpt.strategy.adapters import adapter_registry, get_adapter
from quantgpt.strategy.us_adapter import USResearchAdapter
from quantgpt.us_data.alpha_vantage import AlphaVantageDailyProvider
from quantgpt.us_data.calendar import NewYorkCalendar
from quantgpt.us_data.contracts import DataCapabilityError, PriceRequest, SecurityMapping
from quantgpt.us_data.http import JSONClient, RequestGate
from quantgpt.us_data.sec import SECCompanyFactsProvider, select_asof, ttm_from_quarters

FIXTURES = Path(__file__).parent / "fixtures/us_data"


def payload(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def json_client(body=None, handler=None, cancel_check=None):
    transport = httpx.MockTransport(handler or (lambda request: httpx.Response(200, json=body)))
    return JSONClient(client=httpx.Client(transport=transport), gate=RequestGate(0), cancel_check=cancel_check)


@pytest.fixture(scope="module")
def calendar():
    return NewYorkCalendar()


def price_request(**changes):
    return replace(PriceRequest(SecurityMapping("security:test-1", "IBM", date(2020, 1, 1)),
                                date(2024, 3, 8), date(2024, 3, 12),
                                datetime(2024, 3, 13, tzinfo=timezone.utc)), **changes)


def test_calendar_uses_new_york_dst_holidays_and_half_days(calendar):
    assert calendar.sessions(date(2024, 3, 8), date(2024, 3, 8))[0].opens_at.hour == 14
    assert calendar.sessions(date(2024, 3, 11), date(2024, 3, 11))[0].opens_at.hour == 13
    assert calendar.sessions(date(2024, 7, 4), date(2024, 7, 4)) == ()
    half_day = calendar.sessions(date(2024, 11, 29), date(2024, 11, 29))[0]
    assert half_day.closes_at.hour == 18  # 13:00 EST
    assert calendar.sessions(date(2025, 1, 9), date(2025, 1, 9)) == ()  # Carter day of mourning


def test_daily_raw_identity_provenance_and_asof(calendar):
    provider = AlphaVantageDailyProvider(http=json_client(payload("alpha_daily.json")), calendar=calendar)
    batch = provider.fetch_prices(price_request(asof=datetime(2024, 3, 11, 19, tzinfo=timezone.utc)))
    assert batch.prices["security_id"].tolist() == ["security:test-1"]
    assert batch.prices["close"].tolist() == [102.0]
    assert batch.provenance.status == "research_only"
    assert batch.provenance.adjustment == "raw"
    assert batch.provenance.upstream_version == "unknown"
    assert "public_demo" in batch.provenance.limitations
    assert len(batch.provenance.source_sha256) == 64
    assert batch.next_cursor is None


def test_internal_hole_is_explicit_and_content_changes_hash(calendar):
    body = payload("alpha_daily.json")
    original = AlphaVantageDailyProvider(http=json_client(body), calendar=calendar).fetch_prices(price_request())
    del body["Time Series (Daily)"]["2024-03-11"]
    changed = AlphaVantageDailyProvider(http=json_client(body), calendar=calendar).fetch_prices(price_request())
    assert changed.missing_sessions == ("2024-03-11",)
    assert original.provenance.source_sha256 != changed.provenance.source_sha256
    assert original.provenance.request_sha256 == changed.provenance.request_sha256


def test_ticker_validity_prevents_relabeling_old_history(calendar):
    provider = AlphaVantageDailyProvider(http=json_client({}), calendar=calendar)
    with pytest.raises(DataCapabilityError, match="mapping"):
        provider.fetch_prices(price_request(security=SecurityMapping("stable-id", "NEW", date(2025, 1, 1))))
    with pytest.raises(DataCapabilityError) as actions:
        provider.fetch_corporate_actions(price_request())
    assert actions.value.error_code == "CORPORATE_ACTIONS_UNAVAILABLE"
    with pytest.raises(DataCapabilityError) as universe:
        provider.fetch_historical_universe("sp500", date(2024, 3, 8))
    assert universe.value.error_code == "HISTORICAL_UNIVERSE_UNAVAILABLE"


def test_http_quota_does_not_retry_or_expose_keys():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "60"})

    http = json_client(handler=handler)
    with pytest.raises(DataCapabilityError) as error:
        http.get("https://www.alphavantage.co/query", params={"apikey": "test-secret-value"})
    assert error.value.retryable
    assert error.value.error_code == "PROVIDER_RATE_LIMITED"
    assert "test-secret-value" not in str(error.value.to_dict())
    assert len(calls) == 1


def test_cancel_before_request_and_disallow_unapproved_urls():
    calls = []

    def cancel():
        raise InterruptedError("cancel_requested")

    http = json_client(handler=lambda request: calls.append(request), cancel_check=cancel)
    with pytest.raises(InterruptedError):
        http.get("https://data.sec.gov/submissions/CIK0001234567.json")
    assert calls == []
    with pytest.raises(DataCapabilityError) as error:
        json_client({}).get("https://example.com/credentials")
    assert error.value.error_code == "PROVIDER_URL_REJECTED"


def test_sec_units_vintages_and_date_only_publication():
    provider = SECCompanyFactsProvider("Test Research real-contact@local.test", http=json_client(payload("sec_companyfacts.json")))
    facts = provider.fetch_facts("1234567", fields=("net_income",))
    assert len(facts) == 2
    assert {fact.unit for fact in facts} == {"USD"}
    assert select_asof(facts, datetime(2024, 5, 1, 22, tzinfo=timezone.utc)) == ()
    assert select_asof(facts, datetime(2024, 5, 2, 14, tzinfo=timezone.utc))[0].value == 100
    assert select_asof(facts, datetime(2024, 8, 2, 14, tzinfo=timezone.utc))[0].value == 90
    assert facts[0].issuer_cik == "0001234567"
    with pytest.raises(DataCapabilityError) as unavailable:
        provider.fetch_facts("1234567", fields=("total_assets",))
    assert unavailable.value.error_code == "FIELD_UNAVAILABLE"
    with pytest.raises(DataCapabilityError) as proxy:
        provider.fetch_facts("1234567", fields=("market_cap",))
    assert proxy.value.error_code == "FIELD_NOT_EQUIVALENT"


def test_ttm_rejects_ytd_overlap_and_mixed_units():
    provider = SECCompanyFactsProvider("Test Research real-contact@local.test", http=json_client(payload("sec_companyfacts.json")))
    original = provider.fetch_facts("1234567", fields=("net_income",))[0]
    periods = [("2023-01-01", "2023-03-31"), ("2023-04-01", "2023-06-30"),
               ("2023-07-01", "2023-09-30"), ("2023-10-01", "2023-12-31")]
    quarters = tuple(replace(original, period_start=date.fromisoformat(start), period_end=date.fromisoformat(end))
                     for start, end in periods)
    asof = datetime(2024, 6, 1, tzinfo=timezone.utc)
    assert ttm_from_quarters(quarters, asof=asof) == 400
    with pytest.raises(DataCapabilityError):
        ttm_from_quarters(quarters[:3] + (replace(quarters[-1], period_start=date(2023, 1, 1)),), asof=asof)
    with pytest.raises(DataCapabilityError):
        ttm_from_quarters(quarters[:3] + (replace(quarters[-1], unit="EUR"),), asof=asof)


def test_submissions_pagination_and_budget():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path.endswith("CIK0001234567.json"):
            return httpx.Response(200, json={"cik": "1234567", "filings": {
                "recent": {"accessionNumber": ["recent"]},
                "files": [{"name": "CIK0001234567-submissions-001.json"}],
            }})
        return httpx.Response(200, json={"accessionNumber": ["old"]})

    provider = SECCompanyFactsProvider("Test Research real-contact@local.test", http=json_client(handler=handler))
    assert len(provider.fetch_submissions(1234567)) == 2
    assert len(seen) == 2
    with pytest.raises(DataCapabilityError) as error:
        provider.fetch_submissions(1234567, max_pages=1)
    assert error.value.error_code == "PAGINATION_BUDGET_EXCEEDED"
    assert len(seen) == 3


def test_us_registry_and_factor_dispatch_use_same_adapter(calendar, monkeypatch):
    provider = AlphaVantageDailyProvider(http=json_client(payload("alpha_daily.json")), calendar=calendar)
    adapter = USResearchAdapter(provider, {"test_cohort": (price_request().security,)})
    monkeypatch.setitem(adapter_registry._adapters, "us", adapter)
    assert get_adapter("us") is adapter
    result = compute_factor_values_payload("close", "test_cohort", "2024-03-08", "2024-03-12", market="us")
    assert result["data"][0]["values"] == {"security:test-1": 102.0}
    assert result["research_only"] is True
    assert result["status"] == "research_only"
    assert result["data_provenance"][0]["adjustment"] == "raw"
    with pytest.raises(DataCapabilityError) as remote:
        compute_factor_values_payload("close", "test_cohort", "2024-03-08", "2024-03-12",
                                      market="us", allow_remote_fetch=False)
    assert remote.value.error_code == "REMOTE_FETCH_DISABLED"
    request = validate_factor_values_request("ts_mean(close, 2)", "test_cohort", "2024-03-11", "2024-03-12", market="us")
    assert request.fetch_start == "2024-03-08"


@pytest.mark.asyncio
async def test_us_factor_rest_actual_service(client, auth_headers, calendar, monkeypatch):
    provider = AlphaVantageDailyProvider(http=json_client(payload("alpha_daily.json")), calendar=calendar)
    monkeypatch.setitem(adapter_registry._adapters, "us", USResearchAdapter(provider, {"test_cohort": (price_request().security,)}))
    response = await client.post("/api/v1/factor_values", headers=auth_headers, json={
        "expression": "close", "market": "us", "universe": "test_cohort", "backend": "local",
        "start_date": "2024-03-08", "end_date": "2024-03-12",
    })
    assert response.status_code == 200
    assert response.json()["data"][-1]["values"] == {"security:test-1": 104.0}
    assert response.json()["research_only"] is True
    blocked = await client.post("/api/v1/factor_values", headers=auth_headers, json={
        "expression": "close", "market": "us", "universe": "test_cohort", "allow_remote_fetch": False,
        "start_date": "2024-03-08", "end_date": "2024-03-12",
    })
    assert blocked.status_code == 400
    assert blocked.json()["detail"]["error_code"] == "REMOTE_FETCH_DISABLED"
