"""Exact-byte provider response replay, exercised with mocked requests only."""

import base64
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from filelock import Timeout

from quantgpt.us_data import cache as cache_module
from quantgpt.us_data.cache import CachedJSONClient
from quantgpt.us_data.contracts import DataCapabilityError
from quantgpt.us_data.http import RequestGate

ALPACA = "https://data.alpaca.markets/v2/stocks/bars"
SEC = "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"
HEADERS = {"APCA-API-KEY-ID": "private-key-id", "APCA-API-SECRET-KEY": "private-secret",
           "User-Agent": "QuantGPT private-contact@example.com"}


def client(root, handler, **kwargs):
    return CachedJSONClient(root, client=httpx.Client(transport=httpx.MockTransport(handler)),
                            gate=RequestGate(0), **kwargs)


@pytest.mark.parametrize("url", [ALPACA, SEC, "https://data.sec.gov/submissions/CIK0000320193-submissions-001.json"])
def test_exact_bytes_hash_time_and_header_privacy_survive_replay(tmp_path, url):
    raw = b'{  "value": 17, "labels": ["first", "second"]  }\n'
    seen = []

    def fetch(req):
        seen.append(req)
        assert req.headers["APCA-API-SECRET-KEY"] == HEADERS["APCA-API-SECRET-KEY"]
        return httpx.Response(200, content=raw)

    original = client(tmp_path, fetch).get(url, headers=HEADERS)
    replay = client(tmp_path, lambda req: pytest.fail("cache replay reached HTTP")).get(url, headers=HEADERS)
    assert replay == original
    assert replay.raw == raw
    assert replay.sha256 == hashlib.sha256(raw).hexdigest()
    assert len(seen) == 1
    stored = next(tmp_path.glob("*.json")).read_text()
    assert all(value not in stored for value in HEADERS.values())
    assert all(name not in stored for name in HEADERS)
    assert url not in stored


def test_query_order_has_one_identity_and_different_semantics_get_distinct_entries(tmp_path):
    seen = []

    def fetch(req):
        seen.append(req)
        return httpx.Response(200, json={"symbol": req.url.params["symbols"]})

    source = client(tmp_path, fetch)
    one = source.get(ALPACA, params={"symbols": "IBM", "feed": "sip"})
    assert source.get(ALPACA + "?feed=sip&symbols=IBM") == one
    assert source.get(ALPACA, params={"feed": "sip", "symbols": "IBM"}) == one
    assert source.get(ALPACA, params={"feed": "sip", "symbols": "MSFT"}).data["symbol"] == "MSFT"
    assert len(seen) == 2
    assert len(list(tmp_path.glob("*.json"))) == 2


def test_refresh_replaces_bytes_but_network_failure_preserves_previous_cache(tmp_path):
    one = client(tmp_path, lambda req: httpx.Response(200, json={"value": 1})).get(SEC)
    source = client(tmp_path, lambda req: httpx.Response(200, json={"value": 2}), refresh=True)
    two = source.get(SEC)
    assert two.sha256 != one.sha256
    with pytest.raises(DataCapabilityError) as failure:
        client(tmp_path, lambda req: httpx.Response(503), refresh=True).get(SEC)
    assert failure.value.error_code == "PROVIDER_HTTP_ERROR"
    assert client(tmp_path, lambda req: pytest.fail("replay fetched HTTP")).get(SEC) == two


@pytest.mark.parametrize("change", [
    {"schema_version": "unknown"}, {"request_sha256": "wrong"}, {"source_sha256": "wrong"},
    {"body_base64": "not base64!"}, {"retrieved_at": "2024-01-01T00:00:00"},
])
def test_corrupt_cache_is_fail_closed_without_network_fallback(tmp_path, change):
    client(tmp_path, lambda req: httpx.Response(200, json={"value": 1})).get(SEC)
    path = next(tmp_path.glob("*.json"))
    entry = json.loads(path.read_text())
    path.write_text(json.dumps({**entry, **change}))
    with pytest.raises(DataCapabilityError) as failure:
        client(tmp_path, lambda req: pytest.fail("corruption silently refetched")).get(SEC)
    assert failure.value.error_code == "CACHE_INVALID"


@pytest.mark.parametrize("raw", [b"broken", b"[]", b'{"value": NaN}'])
def test_corrupt_body_rejected_even_when_its_hash_matches(tmp_path, raw):
    client(tmp_path, lambda req: httpx.Response(200, json={"value": 1})).get(SEC)
    path = next(tmp_path.glob("*.json"))
    entry = json.loads(path.read_text())
    entry.update(body_base64=base64.b64encode(raw).decode(), source_sha256=hashlib.sha256(raw).hexdigest())
    path.write_text(json.dumps(entry))
    with pytest.raises(DataCapabilityError) as failure:
        client(tmp_path, lambda req: pytest.fail("invalid body silently refetched")).get(SEC)
    assert failure.value.error_code == "CACHE_INVALID"


def test_concurrent_instances_fetch_once_and_replay_identical_result(tmp_path):
    calls = []
    start = threading.Barrier(6)

    def worker(index):
        def fetch(req):
            calls.append(index)
            return httpx.Response(200, json={"writer": index})

        start.wait(timeout=5)
        return client(tmp_path, fetch).get(ALPACA, params={"symbols": "IBM", "feed": "sip"})

    with ThreadPoolExecutor(max_workers=6) as executor:
        responses = list(executor.map(worker, range(6)))
    assert len(calls) == 1
    assert all(response == responses[0] for response in responses)
    assert len(list(tmp_path.glob("*.json"))) == 1
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize(("url", "params"), [
    (ALPACA, {"api_key": "private-secret"}), (ALPACA + "?apikey=private-secret", {"symbols": "IBM"}),
    (ALPACA + "?symbols=IBM&symbols=MSFT", None), (ALPACA, {"symbols": "private-secret"}),
    ("https://www.alphavantage.co/query", None), ("https://other.invalid/data", None),
    ("http://data.alpaca.markets/v2/stocks/bars", None), (SEC + "?apikey=private-secret", None),
])
def test_unapproved_or_key_bearing_requests_are_rejected_before_network_and_disk(tmp_path, url, params):
    with pytest.raises(DataCapabilityError) as failure:
        client(tmp_path, lambda req: pytest.fail("unapproved request fetched")).get(url, params=params, headers=HEADERS)
    assert failure.value.error_code == "CACHE_REQUEST_REJECTED"
    assert "private-secret" not in str(failure.value)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("private", list(HEADERS.values()))
def test_private_header_echoes_never_reach_disk(tmp_path, private):
    with pytest.raises(DataCapabilityError) as failure:
        client(tmp_path, lambda req: httpx.Response(200, json={"echo": private})).get(SEC, headers=HEADERS)
    assert failure.value.error_code == "CACHE_SECRET_REJECTED"
    assert private not in str(failure.value)
    assert not list(tmp_path.glob("*.json"))


@pytest.mark.parametrize("response", [httpx.Response(429), httpx.Response(503),
                                       httpx.Response(200, content=b"bad-json"), httpx.Response(200, json=[])])
def test_failed_requests_do_not_write_cache(tmp_path, response):
    with pytest.raises(DataCapabilityError):
        client(tmp_path, lambda req: response).get(SEC)
    assert not list(tmp_path.glob("*.json"))


def test_atomic_publish_failure_preserves_old_entry_and_cleans_temporary_file(tmp_path, monkeypatch):
    one = client(tmp_path, lambda req: httpx.Response(200, json={"value": 1})).get(SEC)

    def fail_replace(source, destination):
        raise OSError("private path")

    monkeypatch.setattr(cache_module.os, "replace", fail_replace)
    with pytest.raises(DataCapabilityError) as failure:
        client(tmp_path, lambda req: httpx.Response(200, json={"value": 2}), refresh=True).get(SEC)
    assert failure.value.error_code == "CACHE_IO_ERROR"
    assert client(tmp_path, lambda req: pytest.fail("replay fetched HTTP")).get(SEC) == one
    assert not list(tmp_path.glob("*.tmp"))


def test_lock_wait_is_bounded_and_cancellation_is_preserved(tmp_path, monkeypatch):
    def busy_lock(path, *, timeout):
        assert timeout == 30
        raise Timeout(path)

    monkeypatch.setattr(cache_module, "FileLock", busy_lock)
    with pytest.raises(DataCapabilityError) as failure:
        client(tmp_path, lambda req: pytest.fail("lock failure fetched HTTP")).get(SEC)
    assert failure.value.error_code == "CACHE_BUSY"
    assert failure.value.retryable is True

    def cancel():
        raise InterruptedError("cancelled")

    with pytest.raises(InterruptedError):
        client(tmp_path, lambda req: pytest.fail("cancelled request fetched HTTP"), cancel_check=cancel).get(SEC)
