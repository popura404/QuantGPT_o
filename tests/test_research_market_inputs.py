"""Decision-time, frozen-cohort and raw-price contracts at their numerical entry points."""

import pandas as pd
import pytest

from quantgpt.data_snapshots import freeze_market_frame, load_frozen_market_frame
from quantgpt.research.contracts import US_RESEARCH_FIELDS, EvaluationConfigV1, FactorDefinitionV1, SimulationConfigV1
from quantgpt.research.ledger import ResearchLedger
from quantgpt.research.market_inputs import check_dynamic_membership, check_signal_availability, price_basis
from quantgpt.research.portfolio import PortfolioOptimizationConfig, optimize_portfolio
from quantgpt.strategy.backtest import StrategyBacktestRequest, run_strategy_backtest
from quantgpt.strategy.spec import StrategySpecV1


def test_exchange_early_close_and_field_specific_vintages():
    frame = pd.DataFrame({"trade_date": ["2024-11-29"], "revenue": [12], "revenue_revision_id": ["filing-1"],
                          "available_at": ["2024-11-29T18:00:00Z"], "revenue_available_at": ["2024-11-29T18:01:00Z"]})
    scope = {"calendar_id": "XNYS", "timezone": "America/New_York"}
    revenue = next(field for field in US_RESEARCH_FIELDS if field.name == "revenue")
    with pytest.raises(ValueError, match="PIT_INPUT_AFTER_SIGNAL: revenue_available_at"):
        check_signal_availability(frame, [revenue], **scope)
    frame["decision_at"] = "2024-11-29T18:05:00Z"
    assert check_signal_availability(frame, [revenue], **scope)["status"] == "checked"
    with pytest.raises(ValueError, match="PIT_FIELD_VINTAGE_REQUIRED"):
        check_signal_availability(frame.drop(columns="revenue_revision_id"), ["revenue"], **scope)
    with pytest.raises(ValueError, match="PIT_DECISION_BEFORE_CLOSE"):
        check_signal_availability(frame.assign(decision_at="2024-11-29T17:59:00Z"), [revenue], **scope)
    unknown = check_signal_availability(frame.drop(columns="available_at"), ["close"], **scope)
    assert unknown["status"] == "unverified" and unknown["unverified_fields"] == ["close"]


def test_dividend_receivable_stays_in_nav_without_financing_purchases():
    book = ResearchLedger(SimulationConfigV1(initial_cash=100, rebalance_anchor_session="2024-06-03"))
    book.step("2024-06-03", {"A": {"open": 100, "close": 100}}, targets={"A": 1})
    prices = {"A": {"open": 99, "close": 99}, "B": {"open": 10, "close": 10}}
    event = {"event_id": "A-dividend", "security_id": "A", "type": "cash_dividend", "per_share": 1,
             "pay_session": "2024-06-06"}
    ex = book.step("2024-06-04", prices, targets={"A": .5, "B": .5}, actions=[event])
    assert ex["nav"] == pytest.approx(100) and ex["cash"] == pytest.approx(0)
    assert ex["positions"] == pytest.approx({"A": .5, "B": 4.95})
    assert ex["receivables"]["A-dividend"] == pytest.approx(1)
    unchanged = book.step("2024-06-05", prices, targets={"A": .5, "B": .5}, max_turnover=0)
    assert not unchanged["skipped"] and unchanged["turnover"] == pytest.approx(0)
    paid = book.step("2024-06-06", prices, targets={"B": 1})
    assert paid["positions"] == pytest.approx({"B": 10}) and paid["receivables"] == {}


def test_optimizer_accepts_provider_raw_ohlcv_and_declines_unknown_basis():
    dates = pd.bdate_range("2024-02-01", periods=75)
    panel = pd.DataFrame({"trade_date": dates, "stock_code": "A", "open": range(100, 175), "close": range(100, 175),
                          "volume": 1000000, "adjustment": "raw"})
    signals = panel[["trade_date", "stock_code"]].assign(eligibility=True)
    settings = PortfolioOptimizationConfig(asof_session=dates[-1].date(), max_asset_weight=1, max_adv_participation=.1,
        training_end=dates[35].date(), validation_end=dates[55].date(), risk_aversion_candidates=(1, 3))
    assert optimize_portfolio(signals, panel, settings)["performance_observed_trials"] > 0
    assert optimize_portfolio(signals, panel.drop(columns="adjustment"), settings)["error_code"] == "VALIDATION_RAW_PRICES_REQUIRED"
    assert price_basis(panel) == "raw"
    with pytest.raises(ValueError, match="CONFLICTING_PRICE_BASIS"):
        price_basis(panel.assign(adjustment=["split_adjusted", *["raw"] * 74]))


def test_single_and_composite_provider_provenance_survive_freezing():
    panel = pd.DataFrame({"close": [100, 101]})
    panel.attrs["data_provenance"] = {"provider": "single", "adjustment": "unadjusted"}
    assert price_basis(panel) == "raw"
    panel.attrs["data_snapshot"] = {"source_metadata": {"input_attributes": {
        "data_provenance": [{"adjustment": "raw"}, {"adjustment": "none"}]}}}
    del panel.attrs["data_provenance"]
    assert price_basis(panel) == "raw"
    panel.attrs["data_provenance"] = {"adjustment": "total_return"}
    with pytest.raises(ValueError, match="CONFLICTING_PRICE_BASIS"):
        price_basis(panel)
    panel.attrs = {"data_provenance": [{"adjustment": "raw"}, {"adjustment": "unknown"}]}
    assert price_basis(panel) == "unknown"


def test_dynamic_scope_requires_matching_known_daily_members(tmp_path):
    from quantgpt.research.demo import prepare_offline_demo

    cfg = prepare_offline_demo(tmp_path)["config"]
    cfg["scope"]["universe_mode"] = "dynamic_pit"
    scope = EvaluationConfigV1.model_validate(cfg).scope
    panel = pd.DataFrame({"session": ["2024-06-03", "2024-06-04"], "security_id": ["A", "B"]})
    end = pd.Timestamp("2024-06-04").date()
    with pytest.raises(ValueError, match="PIT_UNIVERSE_MEMBERSHIP_REQUIRED"):
        check_dynamic_membership(panel, scope, end)
    intervals = [
        {"security_id": "A", "valid_from": "2024-01-01", "valid_to": "2024-06-04", "available_at": "2023-12-31T12:00:00Z"},
        {"security_id": "B", "valid_from": "2024-06-04", "valid_to": None, "available_at": "2024-06-04T12:00:00Z"},
    ]
    panel.attrs["universe_membership"] = intervals
    check_dynamic_membership(panel, scope, end)
    intervals[1]["available_at"] = "2024-06-05T12:00:00Z"
    with pytest.raises(ValueError, match="PIT_UNIVERSE_MEMBERSHIP_MISMATCH"):
        check_dynamic_membership(panel, scope, end)
    check_dynamic_membership(panel, scope, pd.Timestamp("2024-06-03").date())


@pytest.mark.asyncio
async def test_existing_unknown_grant_keeps_the_same_usable_input_reference(db_session, test_user, tmp_path):
    from sqlalchemy import select

    from quantgpt.models import ResearchAuditEvent
    from quantgpt.research.evaluations import authorize_data_inputs, load_evaluation_panel
    from quantgpt.research.preparation import prepare_project_demo, register_project_snapshot
    from quantgpt.research.projects import create_project

    project = await create_project(db_session, test_user.id, name="Historical grant")
    demo = await prepare_project_demo(db_session, test_user.id, project.id, snapshot_root=tmp_path)
    ref = {**demo["config"]["data_inputs"][0], "adjustment": "unknown"}
    event = (await db_session.scalars(select(ResearchAuditEvent).where(
        ResearchAuditEvent.project_id == project.id, ResearchAuditEvent.action == "snapshot_registered"))).one()
    event.payload = {**event.payload, "data_input": ref}
    await db_session.commit()
    renewed = await register_project_snapshot(db_session, test_user.id, project.id, ref["manifest_id"], snapshot_root=tmp_path)
    assert renewed["adjustment"] == "raw" and renewed["data_input"] == ref
    cfg = EvaluationConfigV1.model_validate({**demo["config"], "data_inputs": [renewed["data_input"]]})
    await authorize_data_inputs(db_session, project.id, cfg)
    assert price_basis(load_evaluation_panel(cfg, tmp_path)) == "raw"


@pytest.mark.asyncio
async def test_registered_delayed_panel_blocks_factor_and_strategy_with_custom_us_cohort(db_session, test_user, tmp_path):
    from quantgpt.research.evaluations import evaluate_factor_batch, load_evaluation_panel
    from quantgpt.research.preparation import prepare_project_demo, register_project_snapshot
    from quantgpt.research.projects import create_project

    private, public = tmp_path / "private", tmp_path / "public"
    project = await create_project(db_session, test_user.id, name="Imported cohort")
    demo = await prepare_project_demo(db_session, test_user.id, project.id, snapshot_root=private)
    cfg = demo["config"]
    panel = load_frozen_market_frame(cfg["data_inputs"][0]["manifest_id"], private)
    panel["available_at"] = pd.to_datetime(panel["trade_date"], utc=True) + pd.Timedelta(days=300)
    panel = panel.rename(columns={"trade_date": "session", "stock_code": "security_id"})
    manifest = freeze_market_frame(panel, public, vendor="late-publication-test")
    imported = await register_project_snapshot(db_session, test_user.id, project.id, manifest["snapshot_id"],
                                               snapshot_root=private, import_root=public)
    cfg["data_inputs"] = [imported["data_input"]]
    cfg["scope"].update(market="us_equity", universe_id="uploaded_sector_cohort")
    config = EvaluationConfigV1.model_validate(cfg)
    panel = load_evaluation_panel(config, private)
    assert panel["stock_code"].equals(panel["security_id"])
    result = (await evaluate_factor_batch(db_session, test_user.id, project.id,
        [FactorDefinitionV1.model_validate(demo["definitions"][0])], config, snapshot_root=private))["evaluations"][0]
    assert not result["summary"]["performance_observed"] and "PIT_INPUT_AFTER_SIGNAL" in result["failure_reason"]
    spec = {key: value for key, value in demo["strategy_template"].items()
            if key not in {"factor_evaluations", "semantics_version", "simulation_config"}}
    spec.update(schema_version="strategy_spec/v1", market=config.scope.market, universe=config.scope.universe_id)
    request = StrategyBacktestRequest(spec=StrategySpecV1.model_validate(spec), simulation_config=config.simulation_config,
        start_date=str(config.window.start_session), end_date=str(config.window.end_session), benchmark=config.scope.benchmark)
    panel.attrs.update(frozen_research_input=True, research_scope=cfg["scope"])
    with pytest.raises(ValueError, match="PIT_INPUT_AFTER_SIGNAL"):
        run_strategy_backtest(request, panel)
    panel["available_at"] = pd.to_datetime(panel["trade_date"], utc=True) + pd.Timedelta(hours=12)
    evaluated = run_strategy_backtest(request, panel)
    assert evaluated.diagnostics["signal_availability"]["status"] == "checked" and not evaluated.strategy_returns.empty
