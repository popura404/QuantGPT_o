"""Alpaca SIP acceptance through simulated HTTP responses, without factor evaluation."""

import json
from dataclasses import replace
from datetime import date, datetime, timezone

import httpx
import pytest

from quantgpt.us_data.alpaca import AlpacaDailyProvider
from quantgpt.us_data.alpha_vantage import AlphaVantageDailyProvider
from quantgpt.us_data.calendar import NewYorkCalendar
from quantgpt.us_data.contracts import DataCapabilityError, PriceRequest, SecurityMapping
from quantgpt.us_data.http import JSONClient, RequestGate


@pytest.fixture(scope="module")
def calendar():
    return NewYorkCalendar()


def request(**changes):
    return replace(PriceRequest(
        SecurityMapping("security:test", "IBM", date(2000, 1, 1), identity_source="test-mapping"),
        date(2024, 3, 8), date(2024, 3, 12), datetime(2024, 3, 13, 12, tzinfo=timezone.utc), feed="sip",
    ), **changes)


def bar(day, **changes):
    offset = "05:00:00Z" if day <= "2024-03-08" else "04:00:00Z"
    return {"t": f"{day}T{offset}", "o": 100, "h": 106, "l": 99, "c": 102, "v": 1000, **changes}


def provider(calendar, handler, **kwargs):
    http = JSONClient(client=httpx.Client(transport=httpx.MockTransport(handler)), gate=RequestGate(0))
    return AlpacaDailyProvider("fake-key-id", "fake-secret", http=http, calendar=calendar, **kwargs)


def test_full_sip_history_is_paginated_raw_and_records_day_end_availability(calendar):
    seen = []

    def handle(req):
        seen.append(req)
        assert req.url.params["feed"] == "sip"
        assert req.url.params["timeframe"] == "1Day"
        assert req.url.params["adjustment"] == "raw"
        assert req.url.params["asof"] == "-"
        assert req.headers["APCA-API-KEY-ID"] == "fake-key-id"
        assert req.headers["APCA-API-SECRET-KEY"] == "fake-secret"
        if len(seen) == 1:
            return httpx.Response(200, json={"bars": {"IBM": [bar("2024-03-08")]}, "next_page_token": "second"})
        assert req.url.params["page_token"] == "second"
        return httpx.Response(200, json={"bars": {"IBM": [bar("2024-03-11"), bar("2024-03-12")]}, "next_page_token": None})

    source = provider(calendar, handle)
    batch = source.fetch_prices(request())
    assert batch.missing_sessions == ()
    assert len(batch.prices) == 3
    assert batch.prices["stock_code"].tolist() == ["security:test"] * 3
    assert batch.prices["available_at"].tolist() == [
        "2024-03-09T00:15:00-05:00", "2024-03-12T00:15:00-04:00", "2024-03-13T00:15:00-04:00",
    ]
    assert batch.prices["close"].tolist() == [102.0] * 3
    assert batch.provenance.feed == "sip"
    assert batch.provenance.adjustment == "raw"
    assert batch.provenance.provider == "alpaca_sip"
    assert len(batch.provenance.source_sha256) == 64
    assert batch.next_cursor is None
    public = json.dumps(batch.provenance.__dict__) + json.dumps(source.capabilities())
    assert "fake-key-id" not in public and "fake-secret" not in public
    assert all("fake" not in str(req.url) for req in seen)


def test_partial_or_future_session_is_missing_and_never_silently_selected(calendar):
    captured = []

    def handle(req):
        captured.append(req)
        return httpx.Response(200, json={"bars": {"IBM": [bar("2024-03-08")]}, "next_page_token": None})

    # Monday has closed, but its complete daily volume remains unavailable until Tuesday.
    cutoff = datetime(2024, 3, 11, 21, tzinfo=timezone.utc)
    batch = provider(calendar, handle, now=lambda: cutoff).fetch_prices(request())
    assert batch.missing_sessions == ("2024-03-11", "2024-03-12")
    assert batch.prices["session"].tolist() == ["2024-03-08"]
    assert datetime.fromisoformat(captured[0].url.params["end"]) < cutoff


@pytest.mark.parametrize("payload", [
    {"bars": {"MSFT": [bar("2024-03-08")]}},
    {"bars": {"IBM": [bar("2024-03-09")]}},
    {"bars": {"IBM": [bar("2024-03-08", t="2024-03-08T09:30:00-05:00")]}},
    {"bars": {"IBM": [bar("2024-03-08", t="2024-03-08")]}},
    {"bars": {"IBM": [bar("2024-03-08", t="NaT")]}},
    {"bars": {"IBM": [bar("2024-03-08"), bar("2024-03-08")]}},
    {"bars": []}, {},
    {"code": 403, "message": "fake-secret"},
])
def test_malformed_or_wrong_identity_response_fails_without_partial_data(calendar, payload):
    source = provider(calendar, lambda req: httpx.Response(200, json=payload))
    with pytest.raises(DataCapabilityError) as failure:
        source.fetch_prices(request())
    assert "fake-secret" not in json.dumps(failure.value.to_dict())


@pytest.mark.parametrize("changes", [
    {"o": 0}, {"v": -1}, {"h": 99}, {"c": "NaN"}, {"l": "Infinity"}, {"v": True}, {"o": None},
])
def test_invalid_bar_values_are_rejected(calendar, changes):
    source = provider(calendar, lambda req: httpx.Response(200, json={"bars": {"IBM": [bar("2024-03-08", **changes)]}}))
    with pytest.raises(DataCapabilityError):
        source.fetch_prices(request())


def test_duplicate_pagination_token_and_page_budget_do_not_publish_partial_history(calendar):
    calls = []

    def handle(req):
        calls.append(req)
        return httpx.Response(200, json={"bars": {}, "next_page_token": "same"})

    with pytest.raises(DataCapabilityError) as duplicate:
        provider(calendar, handle).fetch_prices(request())
    assert duplicate.value.error_code == "PROVIDER_PAGE_REJECTED"
    assert len(calls) == 2
    calls.clear()
    with pytest.raises(DataCapabilityError) as bounded:
        provider(calendar, handle, max_pages=1).fetch_prices(request())
    assert bounded.value.error_code == "PAGINATION_BUDGET_EXCEEDED"
    assert len(calls) == 1


@pytest.mark.parametrize(("code", "retryable"), [(401, False), (403, False), (429, True), (503, True), (302, False)])
def test_http_errors_are_redacted_and_do_not_retry(calendar, code, retryable):
    calls = []

    def handle(req):
        calls.append(req)
        return httpx.Response(code, json={"message": "fake-secret"}, headers={"Location": "https://elsewhere.invalid"})

    with pytest.raises(DataCapabilityError) as failure:
        provider(calendar, handle).fetch_prices(request())
    assert failure.value.retryable is retryable
    assert "fake-secret" not in json.dumps(failure.value.to_dict())
    assert len(calls) == 1


def test_network_failure_and_cancellation_preserve_secrets(calendar):
    def network(req):
        raise httpx.ConnectError("failed with fake-secret", request=req)

    with pytest.raises(DataCapabilityError) as failure:
        provider(calendar, network).fetch_prices(request())
    assert failure.value.error_code == "PROVIDER_NETWORK_ERROR"
    assert "fake-secret" not in str(failure.value)
    source = provider(calendar, lambda req: pytest.fail("cancelled request reached transport"))

    def cancel():
        raise InterruptedError("cancelled")

    source.http.cancel_check = cancel
    with pytest.raises(InterruptedError):
        source.fetch_prices(request())


def test_mapping_feed_credentials_and_pending_day_are_checked_before_http(calendar, monkeypatch):
    source = provider(calendar, lambda req: pytest.fail("invalid request reached transport"))
    for invalid in [
        request(feed="iex"), request(adjustment="all"),
        request(security=SecurityMapping("new-id", "IBM", date(2025, 1, 1))),
        request(asof=datetime(2024, 3, 8, 21, tzinfo=timezone.utc)),
    ]:
        with pytest.raises(DataCapabilityError):
            source.fetch_prices(invalid)
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    with pytest.raises(DataCapabilityError) as missing:
        AlpacaDailyProvider(http=source.http, calendar=calendar).fetch_prices(request())
    assert missing.value.error_code == "PROVIDER_CREDENTIALS_MISSING"
    with pytest.raises(DataCapabilityError) as old_feed:
        AlphaVantageDailyProvider(http=source.http, calendar=calendar).fetch_prices(request())
    assert old_feed.value.error_code == "FEED_UNSUPPORTED"


def test_missing_sessions_and_source_revision_are_explicit(calendar):
    def fetch(close):
        source = provider(calendar, lambda req: httpx.Response(200, json={"bars": {"IBM": [bar("2024-03-08", c=close)]}}))
        return source.fetch_prices(request())

    old, new = fetch(102), fetch(103)
    assert old.missing_sessions == ("2024-03-11", "2024-03-12")
    assert old.provenance.source_sha256 != new.provenance.source_sha256
    assert old.provenance.request_sha256 == new.provenance.request_sha256
