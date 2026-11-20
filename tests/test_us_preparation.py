"""Mock-only US preparation, immutable replay, and shared project evaluations."""

import json
from datetime import date, datetime, timezone

import httpx
import pandas as pd
import pytest
from sqlalchemy import func, select

from quantgpt.data_quality import run_data_quality_gate
from quantgpt.data_snapshots import load_frozen_market_frame
from quantgpt.expression_parser import parse_expression
from quantgpt.models import Experiment, Task
from quantgpt.research.contracts import EvaluationConfigV1, FactorDefinitionV1
from quantgpt.research.evaluations import evaluate_factor_batch
from quantgpt.research.ledger import prepare_market_context
from quantgpt.research.market_inputs import check_signal_availability
from quantgpt.research.preparation import get_project_preparation, list_project_imports, register_project_snapshot
from quantgpt.research.projects import create_project
from quantgpt.research.returns import daily_asset_returns, forward_total_returns
from quantgpt.strategy.us_adapter import USResearchAdapter
from quantgpt.us_data.alpaca import AlpacaDailyProvider
from quantgpt.us_data.cache import CachedJSONClient
from quantgpt.us_data.contracts import DataCapabilityError
from quantgpt.us_data.http import RequestGate
from quantgpt.us_data.preparation import prepare_us_snapshot
from quantgpt.us_data.sec import SECCompanyFactsProvider


@pytest.fixture
def prepared(tmp_path):
    path = tmp_path / "securities.json"
    path.write_text(json.dumps({"universe_id": "test_cohort", "securities": [
        {"security_id": f"common:{ticker}", "ticker": ticker, "valid_from": "2020-01-01",
         "issuer_cik": "1", "identity_source": "test_verified"} for ticker in ("AAA", "BBB")
    ]}), encoding="utf-8")
    calls = []
    state = {"close": 10.0, "sec_status": 200, "missing": False,
             "first_filed": "2024-04-30", "unpublished_tags": ()}

    def respond(request):
        calls.append(request)
        if request.url.host == "data.alpaca.markets":
            assert request.url.params["feed"] == "sip" and request.url.params["asof"] == "-"
            ticker = request.url.params["symbols"]
            days = ("01", "03") if state["missing"] else ("01", "02", "03")
            bars = []
            for day in days:
                scale = 0.5 if state.get("split") and ticker == "AAA" and day >= "02" else 1
                bars.append({"t": f"2024-05-{day}T04:00:00Z", "o": 10 * scale, "h": 12 * scale,
                             "l": 9 * scale, "c": state["close"] * scale, "v": 1000 / scale})
            return httpx.Response(200, json={"bars": {ticker: bars}, "next_page_token": None})
        assert request.url.host == "data.sec.gov"
        facts = {tag: {"units": {"USD": [
            {"end": "2024-03-31", "filed": "2024-05-10" if tag in state["unpublished_tags"] else filed,
             "val": val, "accn": accession, "form": form}
            for filed, val, accession, form in (
                (state["first_filed"], base, "0000000001-24-000001", "10-Q"),
                ("2024-05-02", base + 1, "0000000001-24-000002", "10-Q/A"),
                ("2024-05-10", base + 99, "0000000001-24-000003", "10-Q/A"),
            )]}} for tag, base in (("Assets", 100), ("Liabilities", 40))}
        return httpx.Response(state["sec_status"], json={"cik": 1, "facts": {"us-gaap": facts}})

    client = httpx.Client(transport=httpx.MockTransport(respond))

    def run(*, refresh=False, fields=("total_assets", "total_liabilities"), actions=None):
        http = CachedJSONClient(tmp_path / "responses", refresh=refresh, client=client, gate=RequestGate(0))
        prices = AlpacaDailyProvider("mock-key", "mock-secret", http=http,
                                     now=lambda: datetime(2025, 1, 1, tzinfo=timezone.utc))
        sec = SECCompanyFactsProvider("Test mock@example.org", http=http) if fields else None
        return prepare_us_snapshot(path, date(2024, 5, 1), date(2024, 5, 3), tmp_path,
                                   prices=prices, fundamentals=sec, fields=fields, corporate_actions_file=actions)

    yield run, calls, state, path
    client.close()


def test_mock_freeze_replay_refresh_and_filing_times(prepared, tmp_path):
    run, calls, state, _ = prepared
    first = run()
    assert len(calls) == 3  # two securities, one shared issuer
    assert first["availability_check"]["status"] == "checked"
    assert first["external_data_acceptance"] == "not_run"
    assert first["scope"]["universe_mode"] == "fixed_cohort"
    panel = load_frozen_market_frame(first["manifest_id"], tmp_path / "imports")
    aaa = panel.loc[panel.security_id == "common:AAA"]
    assert aaa.total_assets.tolist() == [100, 101, 101]
    assert aaa.total_liabilities.tolist() == [40, 41, 41]
    assert str(aaa.iloc[0].decision_at) == "2024-05-02T00:15:00-04:00"
    assert aaa.iloc[0].total_assets_revision_id == "0000000001-24-000001"
    assert panel.attrs["fundamental_provenance"]["issuer_ciks"] == {"common:AAA": "0000000001", "common:BBB": "0000000001"}
    assert "upstream_vintages_unknown" in first["capability_blockers"]
    assert run()["manifest_id"] == first["manifest_id"] and len(calls) == 3
    state["close"] = 11.0
    fresh = run(refresh=True)
    assert len(calls) == 6 and fresh["manifest_id"] != first["manifest_id"]
    assert load_frozen_market_frame(first["manifest_id"], tmp_path / "imports").close.eq(10).all()
    assert run()["manifest_id"] == fresh["manifest_id"] and len(calls) == 6


@pytest.mark.parametrize("problem", ["sec_permission", "missing_price", "missing_cik"])
def test_incomplete_sources_never_publish_a_snapshot(prepared, tmp_path, problem):
    run, calls, state, path = prepared
    if problem == "sec_permission":
        state["sec_status"] = 403
    elif problem == "missing_price":
        state["missing"] = True
    else:
        content = json.loads(path.read_text())
        del content["securities"][0]["issuer_cik"]
        path.write_text(json.dumps(content), encoding="utf-8")
    with pytest.raises(DataCapabilityError):
        run()
    assert not list((tmp_path / "imports").glob("*/manifest.json"))
    if problem == "missing_cik":
        assert not calls


def test_price_only_needs_no_sec_identity(prepared):
    run, calls, _, path = prepared
    content = json.loads(path.read_text())
    for entry in content["securities"]:
        del entry["issuer_cik"]
    path.write_text(json.dumps(content), encoding="utf-8")
    result = run(fields=())
    assert len(calls) == 2 and result["source"] == "alpaca_sip"
    assert {field["name"] for field in result["field_catalog"]} == {"open", "high", "low", "close", "volume"}


def test_legacy_alpaca_snapshot_rejects_missing_actions(prepared, tmp_path):
    run, *_ = prepared
    panel = load_frozen_market_frame(run(fields=())["manifest_id"], tmp_path / "imports")
    for operation in (prepare_market_context, daily_asset_returns, lambda frame: forward_total_returns(frame, 1)):
        with pytest.raises(ValueError, match="ACTION_LEDGER_REQUIRED"):
            operation(panel)


@pytest.fixture
def action_release():
    return {
        "provenance": {"provider": "mock", "dataset": "actions", "release": "v1",
                       "source_ref": "test-source", "license_ref": "test-license"},
        "events": [{"ex_session": "2024-05-02", "type": "split", "event_id": "split-a",
                    "security_id": "common:AAA", "ratio": 2}],
        "coverage": [{"security_id": "common:" + ticker, "start": "2024-05-01", "end": "2024-05-03",
                      "event_types": ["split", "cash_dividend", "delisting"]} for ticker in ("AAA", "BBB")],
    }


def test_freeze_operator_actions_and_replay(prepared, tmp_path, action_release):
    run, _, state, _ = prepared
    state["split"] = True
    original = run(fields=())["manifest_id"]
    path = tmp_path / "actions.json"
    release = action_release
    path.write_text(json.dumps(release))
    result = run(fields=(), actions=path)
    assert result["manifest_id"] != original and result["research_only"]
    panel = load_frozen_market_frame(result["manifest_id"], tmp_path / "imports")
    aaa = panel.loc[panel.ticker == "AAA"]
    assert aaa.close.tolist() == [10, 5, 5]
    assert parse_expression("split_close")(aaa).tolist() == [10, 10, 10]
    assert aaa.split_volume.tolist() == [1000, 1000, 1000]
    assert panel.loc[panel.ticker == "BBB", "split_close"].tolist() == [10, 10, 10]
    assert forward_total_returns(aaa, 1).iloc[0] == pytest.approx(0)
    assert daily_asset_returns(aaa).iloc[1] == pytest.approx(0)
    assert parse_expression("returns")(aaa).iloc[1] == pytest.approx(0)
    assert pd.isna(aaa["returns"].iloc[0])
    assert aaa["returns"].iloc[1:].tolist() == [0.0, 0.0]
    _, quality = run_data_quality_gate(panel.assign(amount=panel.close * panel.volume))
    assert all(issue["rule"] != "extreme_return" for issue in quality["issues"])
    assert prepare_market_context(panel).actions_by_session[pd.Timestamp("2024-05-02")][0]["ratio"] == 2
    assert panel.attrs["data_snapshot"]["source_metadata"]["corporate_action_release"]["event_count"] == 1
    release["coverage"].pop()
    path.write_text(json.dumps(release))
    with pytest.raises(ValueError, match="ACTION_COVERAGE_INCOMPLETE"):
        run(fields=(), actions=path)


@pytest.mark.asyncio
async def test_project_evaluates_returns_from_covered_snapshot(prepared, tmp_path, action_release, db_session, test_user):
    run, _, state, _ = prepared
    state["split"] = True
    for day in ("2024-05-02", "2024-05-03"):
        action_release["events"].append({"ex_session": day, "type": " cash_dividend ", "event_id": f" {day} ",
                                         "security_id": " common:AAA ", "per_share": 0.1, "pay_session": day})
    path = tmp_path / "actions.json"
    path.write_text(json.dumps(action_release))
    frozen = run(fields=(), actions=path)
    private = tmp_path / "private"
    project = await create_project(db_session, test_user.id, name="Covered US returns", market="us")
    registration = await register_project_snapshot(db_session, test_user.id, project.id, frozen["manifest_id"],
                                                    snapshot_root=private, import_root=tmp_path / "imports")
    field = next(item for item in registration["field_catalog"] if item["name"] == "returns")
    assert field["unit"] == "fraction" and field["adjustment"] == "total_return"
    panel = load_frozen_market_frame(frozen["manifest_id"], private)
    assert panel.loc[panel.ticker == "AAA", "returns"].iloc[1:].tolist() == pytest.approx([0.02, 0.02])
    definition = FactorDefinitionV1.model_validate({"expression": "rank(returns)", "fields": [field]})
    config = EvaluationConfigV1.model_validate({
        "backend": "local", "direction": "higher_is_better", "scope": registration["scope"],
        "data_inputs": [registration["data_input"]],
        "window": {"start_session": "2024-05-02", "end_session": "2024-05-03",
                   "split_id": "test_returns", "split_hash": "a" * 64},
        "simulation_config": {"currency": "USD", "rebalance_anchor_session": "2024-05-01",
                              "rebalance_every_sessions": 1},
        "engine": {"engine": "python", "version": "test", "code_version": "test"},
    })
    batch = await evaluate_factor_batch(db_session, test_user.id, project.id, [definition], config, snapshot_root=private)
    evaluation = batch["evaluations"][0]
    assert evaluation["summary"]["performance_observed"], evaluation["failure_reason"]


def test_initial_unpublished_sec_values_remain_missing(prepared, tmp_path):
    run, _, state, _ = prepared
    state["first_filed"] = "2024-05-02"
    partial = run()
    panel = load_frozen_market_frame(partial["manifest_id"], tmp_path / "imports")
    assert panel.loc[panel.session == "2024-05-01", "total_assets"].isna().all()
    assert panel.loc[panel.session != "2024-05-01", "total_assets"].notna().all()


@pytest.mark.parametrize("tags", [("Assets",), ("Assets", "Liabilities")])
def test_completely_unpublished_requested_sec_fields_never_publish(prepared, tmp_path, tags):
    run, _, state, _ = prepared
    state["unpublished_tags"] = tags
    with pytest.raises(DataCapabilityError) as absent:
        run()
    assert absent.value.error_code == "SEC_FIELD_COVERAGE_INCOMPLETE"
    assert not list((tmp_path / "imports").glob("*/manifest.json"))


@pytest.mark.asyncio
async def test_frozen_inputs_register_into_shared_project_without_experiments(prepared, tmp_path, db_session, test_user, monkeypatch):
    run, _, _, _ = prepared
    result = run()
    public, private = tmp_path / "imports", tmp_path / "private"
    project = await create_project(db_session, test_user.id, name="Mock US input acceptance", market="us")
    await db_session.commit()
    monkeypatch.setenv("QUANTGPT_RESEARCH_IMPORT_ROOT", str(public))
    catalog = await list_project_imports(db_session, test_user.id, project.id, snapshot_root=private)
    assert catalog["snapshots"][0]["manifest_id"] == result["manifest_id"]
    registered = await register_project_snapshot(db_session, test_user.id, project.id, result["manifest_id"],
                                                  snapshot_root=private, import_root=public)
    assert registered["data_input"]["version_status"] == "verified"
    assert registered["external_data_acceptance"] == "not_run"
    assert registered["scope"] == result["scope"] and registered["field_catalog"] == result["field_catalog"]
    state = await get_project_preparation(db_session, test_user.id, project.id, snapshot_root=private)
    assert state["snapshots"] == [registered]
    for model in (Experiment, Task):
        assert await db_session.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.parametrize(("session", "decision"), [
    ("2024-11-29", "2024-11-30T00:15:00-05:00"),  # half day, next open Monday
    ("2024-03-08", "2024-03-09T00:15:00-05:00"),  # DST over weekend
    ("2024-05-01", "2024-05-02T00:15:00-04:00"),
])
def test_daily_decisions_can_follow_midnight_before_next_open(session, decision):
    frame = pd.DataFrame({"session": [session], "close": [10.0], "available_at": [decision], "decision_at": [decision]})
    assert check_signal_availability(frame, ["close"], calendar_id="XNYS", timezone="America/New_York")["status"] == "checked"


@pytest.mark.parametrize(("decision", "error"), [
    ("2024-05-02T09:30:00-04:00", "PIT_DECISION_AT_OR_AFTER_NEXT_OPEN"),
    ("2024-05-03T00:15:00-04:00", "PIT_DECISION_AT_OR_AFTER_NEXT_OPEN"),
    ("2024-05-01T15:59:00-04:00", "PIT_DECISION_BEFORE_CLOSE"),
])
def test_daily_decisions_cannot_cross_execution_boundary(decision, error):
    frame = pd.DataFrame({"session": ["2024-05-01"], "close": [10.0], "available_at": [decision], "decision_at": [decision]})
    with pytest.raises(ValueError, match=error):
        check_signal_availability(frame, ["close"], calendar_id="XNYS", timezone="America/New_York")


def test_default_adapter_reports_sip_without_fetching(monkeypatch):
    monkeypatch.delenv("QUANTGPT_US_PRICE_PROVIDER", raising=False)
    monkeypatch.delenv("QUANTGPT_US_SECURITIES_FILE", raising=False)
    monkeypatch.delenv("QUANTGPT_US_FUNDAMENTAL_FIELDS", raising=False)
    capability = USResearchAdapter().capabilities()
    assert capability.provider == "alpaca_sip" and not capability.universes
    assert {"total_assets", "total_liabilities"} <= {field.name for field in capability.data_fields}
    assert "entity_level_not_security_level" in capability.capability_blockers
    assert "entity_level_not_security_level" not in USResearchAdapter(fundamental_fields=()).capabilities().capability_blockers
    monkeypatch.setenv("QUANTGPT_US_PRICE_PROVIDER", "alpha_vantage")
    assert USResearchAdapter().capabilities().universes == ("ibm_demo",)


def test_legacy_adapter_preserves_explicit_fundamental_requests_without_network(monkeypatch):
    monkeypatch.setenv("QUANTGPT_US_PRICE_PROVIDER", "alpha_vantage")
    monkeypatch.delenv("QUANTGPT_US_SECURITIES_FILE", raising=False)
    monkeypatch.delenv("QUANTGPT_US_FUNDAMENTAL_FIELDS", raising=False)
    assert USResearchAdapter().fundamental_fields == ()
    monkeypatch.setenv("QUANTGPT_US_FUNDAMENTAL_FIELDS", "net_income")
    adapter = USResearchAdapter()
    assert adapter.fundamental_fields == ("net_income",)
    with pytest.raises(DataCapabilityError) as missing:
        adapter.fetch_market_data("ibm_demo", "2024-01-02", "2024-01-03")
    assert missing.value.error_code == "SEC_CIK_REQUIRED"
    assert adapter._provider is None
    monkeypatch.setenv("QUANTGPT_US_FUNDAMENTAL_FIELDS", "")
    assert USResearchAdapter().fundamental_fields == ()


def test_cli_uses_documented_settings_without_submitting(monkeypatch, tmp_path, capsys):
    from scripts import freeze_us_research as cli

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APCA_API_KEY_ID", "test-key")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "test-secret")
    observed = {}

    def prepare(*args, **kwargs):
        observed.update(kwargs)
        return {"manifest_id": "mock-id", "external_data_acceptance": "not_run"}

    monkeypatch.setattr(cli, "prepare_us_snapshot", prepare)
    assert cli.main(["--securities", "mapping.json", "--start", "2024-01-01", "--end", "2024-02-01", "--price-only"]) == 0
    assert observed["fields"] == () and observed["fundamentals"] is None
    assert json.loads(capsys.readouterr().out)["manifest_id"] == "mock-id"
