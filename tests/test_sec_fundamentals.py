"""SEC panel preparation from mocked HTTP, without evaluating expressions or strategies."""

import json
from datetime import date, datetime, timezone

import httpx
import pandas as pd
import pytest

from quantgpt.research.market_inputs import check_signal_availability
from quantgpt.us_data.config import load_security_cohort
from quantgpt.us_data.contracts import DataCapabilityError
from quantgpt.us_data.fundamentals import enrich_sec_fundamentals
from quantgpt.us_data.http import JSONClient, RequestGate
from quantgpt.us_data.sec import SECCompanyFactsProvider, select_asof

CIK = "0001234567"


def fact(value, end="2024-03-31", filed="2024-05-01", accession=1, start=None):
    row = {"val": value, "end": end, "filed": filed, "accn": f"{CIK}-24-{accession:06d}", "form": "10-Q"}
    if start is not None:
        row["start"] = start
    return row


def companyfacts():
    return {"cik": 1234567, "facts": {"us-gaap": {
        "Assets": {"units": {"USD": [fact(1000), fact(1100, end="2024-06-30", filed="2024-08-01", accession=2),
                                       fact(950, filed="2024-08-02", accession=3),
                                       fact(1050, end="2024-06-30", filed="2024-08-09", accession=4)]}},
        "Liabilities": {"units": {"USD": [fact(400)]}},
        "NetIncomeLoss": {"units": {"USD": [fact(100, start="2024-01-01"), fact(999, start="2023-04-01"),
            fact(300, end="2024-06-30", filed="2024-08-01", accession=2, start="2024-01-01"),
            fact(200, end="2024-06-30", filed="2024-08-01", accession=2, start="2024-04-01"),
            fact(90, filed="2024-08-02", accession=3, start="2024-01-01")]}},
    }}}


def provider(body, requests):
    def handler(request):
        requests.append(request)
        assert request.url.host == "data.sec.gov"
        assert request.url.path == f"/api/xbrl/companyfacts/CIK{CIK}.json"
        assert request.headers["User-Agent"] == "Synthetic Research contact@local.test"
        return httpx.Response(200, json=body)

    return SECCompanyFactsProvider("Synthetic Research contact@local.test",
        http=JSONClient(client=httpx.Client(transport=httpx.MockTransport(handler)), gate=RequestGate(0)))


def prices():
    days = ["2024-05-01", "2024-05-02", "2024-08-01", "2024-08-02", "2024-08-05", "2024-08-12"]
    return pd.DataFrame([{"security_id": security, "session": day, "close": 10,
                          "decision_at": f"{day}T20:15:00+00:00"} for security in ("share-A", "share-B") for day in days])


def test_panel_facts_follow_publication_and_revision_without_ytd_or_annual_substitution():
    requests = []
    original = prices()
    original.attrs["price_source"] = "synthetic"
    result = enrich_sec_fundamentals(original, {"share-A": CIK, "share-B": CIK},
                                    provider=provider(companyfacts(), requests),
                                    fields=("total_assets", "total_liabilities", "net_income"))
    first = result.iloc[:6]
    assert len(requests) == 1
    assert first.iloc[0][["total_assets", "net_income"]].isna().all()
    assert first["total_assets"].iloc[1:].tolist() == [1000, 1000, 1100, 1100, 1050]
    assert first["net_income"].iloc[1:].tolist() == [100, 100, 200, 200, 200]
    assert first.iloc[-1]["total_assets_revision_id"] == f"{CIK}-24-000004"
    assert first.iloc[-1]["net_income_period_start"].isoformat() == "2024-04-01"
    assert "net_income" not in original
    assert result.attrs["price_source"] == "synthetic"
    provenance = result.attrs["fundamental_provenance"]
    assert provenance["missing_observations"]["net_income"] == 2
    assert len(provenance["sources"][0]["source_sha256"]) == 64
    assert "contact@local.test" not in str(provenance)
    assert check_signal_availability(result, ("net_income",), calendar_id="XNYS",
                                     timezone="America/New_York")["status"] == "checked"


def test_mapping_and_decision_times_are_checked_before_any_request():
    requests = []
    source = provider(companyfacts(), requests)
    with pytest.raises(DataCapabilityError) as missing:
        enrich_sec_fundamentals(prices(), {"share-A": CIK}, provider=source)
    assert missing.value.error_code == "SEC_ISSUER_MAPPING_REQUIRED"
    bad = prices()
    bad["decision_at"] = bad["session"] + "T00:00:00"
    with pytest.raises(DataCapabilityError) as clock:
        enrich_sec_fundamentals(bad, {"share-A": CIK, "share-B": CIK}, provider=source)
    assert clock.value.error_code == "SEC_DECISION_TIME_REQUIRED"
    assert requests == []


def test_explicit_price_only_and_legacy_session_close_work():
    requests = []
    source = provider(companyfacts(), requests)
    original = prices().drop(columns="decision_at")
    pd.testing.assert_frame_equal(enrich_sec_fundamentals(original, {}, provider=source, fields=()), original)
    assert source.fetch_facts(CIK, fields=()) == ()
    assert requests == []
    result = enrich_sec_fundamentals(original, {"share-A": CIK, "share-B": CIK}, provider=source)
    assert pd.isna(result.iloc[0]["total_assets"])
    assert result.iloc[1]["total_assets"] == 1000


def test_next_day_final_daily_bar_uses_facts_available_before_its_decision():
    frame = prices().iloc[:1].copy()
    frame["decision_at"] = "2024-05-02T00:15:00-04:00"
    result = enrich_sec_fundamentals(frame, {"share-A": CIK}, provider=provider(companyfacts(), []))
    assert result.iloc[0]["total_assets"] == 1000
    assert result.iloc[0]["total_assets_available_at"] == datetime(2024, 5, 2, 4, tzinfo=timezone.utc)
    frame["decision_at"] = "2024-05-02T09:30:00-04:00"
    requests = []
    with pytest.raises(DataCapabilityError) as late:
        enrich_sec_fundamentals(frame, {"share-A": CIK}, provider=provider(companyfacts(), requests))
    assert late.value.error_code == "SEC_DECISION_TIME_REQUIRED"
    assert requests == []


def test_flow_requires_exact_quarter_and_rejects_multiple_periods():
    body = companyfacts()
    body["facts"]["us-gaap"]["NetIncomeLoss"]["units"]["USD"] = [fact(400, start="2023-04-01")]
    with pytest.raises(DataCapabilityError) as unavailable:
        enrich_sec_fundamentals(prices(), {"share-A": CIK, "share-B": CIK},
                                provider=provider(body, []), fields=("net_income",))
    assert unavailable.value.error_code == "FIELD_PERIOD_UNAVAILABLE"
    body["facts"]["us-gaap"]["NetIncomeLoss"]["units"]["USD"] = [
        fact(100, start="2024-01-01"), fact(70, start="2024-01-30")]
    with pytest.raises(DataCapabilityError) as ambiguous:
        enrich_sec_fundamentals(prices(), {"share-A": CIK, "share-B": CIK},
                                provider=provider(body, []), fields=("net_income",))
    assert ambiguous.value.error_code == "AMBIGUOUS_FINANCIAL_PERIOD"


def test_same_accession_conflict_is_rejected_regardless_of_input_order():
    body = companyfacts()
    body["facts"]["us-gaap"]["Assets"]["units"]["USD"] = [
        fact(1000), fact(950, filed="2024-08-02", accession=3), fact(900)]
    facts = provider(body, []).fetch_facts(CIK, fields=("total_assets",))
    with pytest.raises(DataCapabilityError) as ambiguous:
        select_asof(facts, datetime(2024, 9, 1, tzinfo=timezone.utc))
    assert ambiguous.value.error_code == "AMBIGUOUS_FINANCIAL_FACT"


@pytest.mark.parametrize("value", [True, None, "bad-number"])
def test_malformed_values_raise_structured_errors(value):
    body = companyfacts()
    body["facts"]["us-gaap"]["Assets"]["units"]["USD"][0]["val"] = value
    with pytest.raises(DataCapabilityError) as malformed:
        provider(body, []).fetch_facts(CIK, fields=("total_assets",))
    assert malformed.value.error_code == "PROVIDER_SCHEMA_ERROR"


def cohort_entry(**changes):
    return {"security_id": "share-A", "ticker": "MOCK", "valid_from": "2020-01-01",
            "valid_to": "2025-01-01", "identity_source": "caller_verified", "issuer_cik": 1234567, **changes}


def load_cohort(tmp_path, entries):
    path = tmp_path / "cohort.json"
    path.write_text(json.dumps({"universe_id": "synthetic_cohort", "securities": entries}), encoding="utf-8")
    return load_security_cohort(path)


def test_cohort_has_explicit_security_identity_and_optional_issuer_identity(tmp_path):
    universe, securities, ciks = load_cohort(tmp_path, [cohort_entry(), cohort_entry(
        security_id="share-B", ticker="MOCK.B", valid_to=None, issuer_cik=CIK)])
    assert universe == "synthetic_cohort"
    assert securities[0].valid_from == date(2020, 1, 1)
    assert securities[1].valid_to is None
    assert ciks == {"share-A": CIK, "share-B": CIK}
    entry = cohort_entry()
    entry.pop("issuer_cik")
    entry.pop("identity_source")
    _, securities, ciks = load_cohort(tmp_path, [entry])
    assert ciks == {}
    assert securities[0].identity_source == "caller_supplied_unverified"


@pytest.mark.parametrize("changes", [
    {"security_id": 123}, {"security_id": " "}, {"ticker": []}, {"ticker": " MOCK"},
    {"identity_source": False}, {"exchange": None}, {"asset_type": "etf"}, {"currency": "EUR"},
    {"valid_from": 20200101}, {"valid_from": "20200101"}, {"valid_from": "2020-02-30"},
    {"valid_to": "2019-12-31"}, {"valid_to": ""}, {"valid_to": False},
    {"issuer_cik": 0}, {"issuer_cik": True}, {"issuer_cik": 1.25}, {"issuer_cik": "12345678901"},
    {"issuer_cik": "١٢٣"}, {"unexpected": "value"},
])
def test_cohort_rejects_invalid_mapping_types_ranges_and_scope(tmp_path, changes):
    with pytest.raises(ValueError):
        load_cohort(tmp_path, [cohort_entry(**changes)])


@pytest.mark.parametrize("entries", [
    [None], [[]], [{"ticker": "MOCK"}], [],
    [cohort_entry(), cohort_entry(ticker="OTHER")],
    [cohort_entry(), cohort_entry(security_id="share-B", ticker="mock")],
])
def test_cohort_rejects_missing_or_duplicate_security_identity(tmp_path, entries):
    with pytest.raises(ValueError):
        load_cohort(tmp_path, entries)


def test_cohort_rejects_repeated_json_keys(tmp_path):
    path = tmp_path / "cohort.json"
    path.write_text('{"universe_id":"first", "universe_id":"second", "securities":[]}', encoding="utf-8")
    with pytest.raises(ValueError, match="repeat object keys"):
        load_security_cohort(path)
