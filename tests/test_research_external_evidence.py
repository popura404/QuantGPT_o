"""Contract-level positive controls and incomplete/contradictory reference releases."""

from copy import deepcopy

import exchange_calendars
import pandas as pd
import pytest

from quantgpt.data_snapshots import freeze_market_frame, load_frozen_market_frame
from quantgpt.research.contracts import EvaluationConfigV1
from quantgpt.research.demo import prepare_offline_demo
from quantgpt.research.external_evidence import validate_external_evidence


@pytest.fixture(scope="module")
def market(tmp_path_factory):
    # Fictitious operator-controlled releases exercise acceptance semantics only.
    root = tmp_path_factory.mktemp("external_releases")
    dates = ["2024-12-23", "2024-12-24", "2024-12-26", "2024-12-27", "2024-12-30", "2024-12-31"]
    calendar = exchange_calendars.get_calendar("XNYS")
    rows = []
    for i, day in enumerate(dates):
        for key, ticker, initial in (("ID-A", "AAA", 45), ("ID-B", "BBB", 73)):
            rows.append({"trade_date": pd.Timestamp(day), "security_id": key, "stock_code": key, "ticker": ticker,
                         "open": initial + i, "high": initial + i + 2, "low": initial + i - 1,
                         "close": initial + i + 1, "volume": 3400, "available_at": calendar.session_close(day).isoformat(),
                         "currency": "USD", "adjustment": "raw"})
    actions = [{"event_id": "split-A", "security_id": "ID-A", "type": "split", "session": dates[2], "ratio": 2.0},
               {"event_id": "div-A", "security_id": "ID-A", "type": "cash_dividend", "session": dates[3],
                "per_share": 1.5, "pay_session": "2025-01-03"},
               {"event_id": "terminal-B", "security_id": "ID-B", "type": "delisting",
                "ex_session": dates[-1], "terminal_value": 0.0}]
    frame = pd.DataFrame(rows)
    frame.attrs.update(calendar_sessions=dates, adjustment="raw", corporate_actions=actions)
    manifest = freeze_market_frame(frame, root, vendor="contract_reference")
    raw = prepare_offline_demo(root)["config"]
    raw["scope"].update(market="us_equity", universe_id="prospective-two", calendar_id="XNYS", benchmark="test-total")
    raw["window"].update(start_session=dates[1], end_session=dates[-1], warmup_observations=1)
    raw["data_inputs"][0].update(manifest_id=manifest["snapshot_id"], content_sha256=manifest["content_hash"].split(":")[-1],
                                 source="contract_reference", feed="ohlcv")
    config = EvaluationConfigV1.model_validate(raw)
    indexes = [100.0, 102.0, 101.0, 108.0, 110.0, 110.0]
    provenance = {"provider": "contract_reference", "dataset": "ohlcv", "release": "approved-test-release",
                  "source_ref": "operator-tested-source", "license_ref": "test-entitlement"}
    bundle = {"schema_version": "external_market_evidence/v1", "scope": config.scope.model_dump(mode="json"),
        "manifest_id": manifest["snapshot_id"], "content_sha256": raw["data_inputs"][0]["content_sha256"],
        "coverage_start": dates[0], "coverage_end": dates[-1],
        "prices": {"provenance": provenance, "currency": "USD", "adjustment": "raw", "rows": [
            {"session": row["trade_date"].date().isoformat(),
             **{k: row[k] for k in ("security_id", "open", "high", "low", "close", "volume")}} for row in rows]},
        "historical_universe": {"provenance": {**provenance, "dataset": "universe"},
            "cohort_selected_at": "2024-01-01T10:00:00Z", "intervals": [
                {"security_id": f"ID-{key}", "ticker": key * 3, "effective_from": "2024-01-01", "effective_to": None,
                 "known_at": "2023-12-30T10:00:00Z"} for key in ("A", "B")]},
        "corporate_actions": {"provenance": {**provenance, "dataset": "events"}, "events": deepcopy(actions),
            "coverage": [{"security_id": key, "start": dates[0], "end": dates[-1],
                          "event_types": ["split", "cash_dividend", "delisting"]} for key in ("ID-A", "ID-B")]},
        "benchmark": {"provenance": {**provenance, "dataset": "index"}, "benchmark_id": "test-total", "currency": "USD",
            "return_type": "total_return", "reinvestment_policy": "Dividends reinvested at ex-date close",
            "observations": [{"session": day, "previous_total_return_level": indexes[i - 1],
                "total_return_level": indexes[i], "return": indexes[i] / indexes[i - 1] - 1}
                for i, day in enumerate(dates) if i]}}
    return load_frozen_market_frame(manifest["snapshot_id"], root), config, bundle


def test_each_release_is_reconciled_and_bound_to_exact_snapshot_and_scope(market):
    frame, config, source = market
    bundle = deepcopy(source)
    report = validate_external_evidence(frame, config, bundle)
    assert report["status"] == "passed" and report["blockers"] == [], report
    assert report["checks"]["prices"]["security_sessions"] == 12
    assert report["checks"]["benchmark"]["sessions"] == 5
    assert report["checks"]["corporate_actions"]["events"] == 3
    assert all(s["status"] == "passed" and s["provenance"]["license_ref"] for s in report["checks"].values())
    assert "observations" not in report["checks"]["benchmark"]
    bundle["scope"]["universe_version"] = "substituted"
    assert validate_external_evidence(frame, config, bundle)["blockers"] == ["EXTERNAL_SCOPE_MISMATCH"]
    bundle["scope"] = source["scope"]
    bundle["content_sha256"] = "0" * 64
    assert validate_external_evidence(frame, config, bundle)["blockers"] == ["EXTERNAL_SNAPSHOT_IDENTITY_MISMATCH"]


def test_research_only_preserves_numeric_reconciliation_without_upgrading_license(market):
    frame, config, original = market
    frame = frame.copy()
    frame.attrs["research_only"] = True
    bundle = deepcopy(original)
    for record in bundle["corporate_actions"]["coverage"]:
        record["event_types"].remove("delisting")
    del bundle["prices"]["provenance"]["license_ref"]
    del bundle["historical_universe"], bundle["benchmark"]
    report = validate_external_evidence(frame, config, bundle)
    assert report["status"] == "insufficient_data" and "RESEARCH_ONLY_SOURCE" in report["blockers"]
    assert report["checks"]["prices"]["security_sessions"] == 12
    assert report["checks"]["prices"]["provenance_issue"]["field"] == "license_ref"
    assert report["checks"]["corporate_actions"]["matched_events"] == 3
    assert report["checks"]["historical_universe"]["status"] == report["checks"]["benchmark"]["status"] == "not_run"
    bundle["prices"]["rows"][0]["open"] += 1
    assert validate_external_evidence(frame, config, bundle)["checks"]["prices"]["reason"] == "PRICE_OBSERVATION_MISMATCH"
    frame.attrs["synthetic"] = True
    assert "SYNTHETIC_SOURCE" in validate_external_evidence(frame, config, original)["blockers"]
    assert validate_external_evidence(frame, config, None)["status"] == "not_run"


@pytest.mark.parametrize(("section", "mutation", "reason"), [
    ("prices", lambda s: s["rows"].pop(), "PRICE_SECURITY_SESSION_COVERAGE_MISMATCH"),
    ("prices", lambda s: s["provenance"].update(provider="invented"), "PRICE_SOURCE_MISMATCH"),
    ("historical_universe", lambda s: s["intervals"][0].update(known_at="2024-12-24T20:00Z"), "MEMBERSHIP_KNOWN_TOO_LATE"),
    ("historical_universe", lambda s: s["intervals"][1].update(ticker="AAA"), "AMBIGUOUS_SECURITY_OR_TICKER_HISTORY"),
    ("historical_universe", lambda s: s.update(cohort_selected_at="2025-01-01T11:00Z"), "COHORT_SELECTED_AFTER_USE"),
    ("corporate_actions", lambda s: s["events"][2].update(terminal_value=55), "FROZEN_CORPORATE_ACTION_MISMATCH"),
    ("corporate_actions", lambda s: s["events"][1].update(pay_session="2024-12-24"), "CORPORATE_ACTION_PAY_BEFORE_ENTITLEMENT"),
    ("benchmark", lambda s: s.update(return_type="price_only"), "BENCHMARK_CONTRACT_MISMATCH"),
    ("benchmark", lambda s: s.update(currency="EUR"), "BENCHMARK_CONTRACT_MISMATCH"),
    ("benchmark", lambda s: s["observations"][1].update({"return": .7}), "BENCHMARK_RETURN_LEVEL_MISMATCH"),
    ("benchmark", lambda s: s["observations"][1].update(previous_total_return_level=10, total_return_level=10,
                                                       **{"return": 0}), "BENCHMARK_LEVEL_CHAIN_BROKEN"),
])
def test_independent_contradictions_fail_only_the_affected_section(market, section, mutation, reason):
    frame, config, original = market
    bundle = deepcopy(original)
    mutation(bundle[section])
    report = validate_external_evidence(frame, config, bundle)
    assert report["status"] == "failed"
    assert report["checks"][section]["reason"] == reason, report
    assert all(v["status"] == "passed" for k, v in report["checks"].items() if k != section)


def test_hash_only_claims_and_incomplete_benchmark_cannot_pass(market):
    frame, config, original = market
    bundle = deepcopy(original)
    bundle["prices"] = {"verified": True, "source_sha256": "a" * 64}
    bundle["benchmark"]["observations"].pop(2)
    report = validate_external_evidence(frame, config, bundle)
    assert report["status"] == "insufficient_data"
    assert report["checks"]["prices"]["status"] == "insufficient_data"
    assert report["checks"]["benchmark"]["reason"] == "BENCHMARK_SESSION_COVERAGE_INCOMPLETE"


def test_selection_does_not_inspect_final_observations_and_exposes_no_prices(market):
    frame, config, original = market
    bundle = deepcopy(original)
    config = config.model_copy(update={"window": config.window.model_copy(update={
        "end_session": pd.Timestamp("2024-12-27").date()})})
    bundle["prices"]["rows"][-1]["volume"] = 0
    bundle["benchmark"]["observations"][-1]["return"] = 7
    report = validate_external_evidence(frame, config, bundle)
    assert report["status"] == "passed", report
    assert report["checks"]["prices"]["security_sessions"] == 8
    assert report["checks"]["benchmark"]["sessions"] == 3
    assert report["checks"]["corporate_actions"]["events"] == 2


def test_explicit_price_publication_before_official_close_cannot_confirm_pit(market):
    frame, config, bundle = market
    raw = frame.copy()
    raw.loc[raw.index[0], "available_at"] = "2024-12-23T12:00:00Z"
    assert validate_external_evidence(raw, config, bundle)["checks"]["prices"]["reason"] == (
        "OFFICIAL_CLOSE_PUBLISHED_BEFORE_EXCHANGE_CLOSE")
