"""Optimization is accepted for feasibility and chronology, not superior returns."""

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from quantgpt.research.portfolio import PortfolioOptimizationConfig, optimize_portfolio


@pytest.mark.parametrize("invalid", [float("inf"), float("nan")])
def test_nonfinite_optimization_assumptions_are_rejected(invalid):
    with pytest.raises(ValidationError):
        PortfolioOptimizationConfig(asof_session="2024-05-01", risk_aversion=invalid)


@pytest.fixture
def panel():
    dates = pd.bdate_range("2024-01-02", periods=75)
    rows = []
    for index, asset in enumerate("ABC"):
        prices = 100 * np.cumprod(1 + .001 * (index + 1) + .002 * np.sin(np.arange(len(dates)) + index))
        for day, price in zip(dates, prices, strict=True):
            rows.append({"security_id": asset, "session": day, "close": price, "raw_close": price,
                         "raw_open": price, "volume": 10_000, "industry": "tech" if asset in "AB" else "utility",
                         "market_cap": 1e9 * (index + 1), "beta": .5 + index * .5})
    return pd.DataFrame(rows)


def signals(panel):
    return panel[["security_id", "session"]].assign(score=1.0, eligibility=True)


def config(panel, **kwargs):
    return PortfolioOptimizationConfig(asof_session=panel["session"].max().date(), max_asset_weight=.6,
                                        lookback_sessions=30, min_observations=20, **kwargs)


def test_constrained_solution_is_feasible_and_reports_economic_residuals(panel):
    settings = config(panel, cash_min=.1, cash_max=.2, industry_max_weights={"tech": .5},
                      beta_max=1.1, log_market_cap_max=22, max_turnover=.9, max_adv_participation=.1)
    result = optimize_portfolio(signals(panel), panel, settings)
    assert result["status"] == "completed" and result["feasible"]
    assert result["max_constraint_violation"] < 1e-7
    weights = {item["security_id"]: item["target_weight"] for item in result["target_weights"]}
    assert weights["A"] + weights["B"] <= .5 + 1e-7
    assert .1 - 1e-7 <= result["cash_weight"] <= .2 + 1e-7
    assert result["turnover"] == pytest.approx(sum(weights.values()))
    assert result["objective"] <= result["baseline"]["objective"] + 1e-7
    assert result["research_decision"] == "not_evaluated" and result["out_of_sample_increment"] is None


def test_future_prices_and_risk_fields_do_not_change_allocation(panel):
    cutoff = sorted(panel["session"].unique())[45]
    settings = config(panel).model_copy(update={"asof_session": pd.Timestamp(cutoff).date()})
    before = optimize_portfolio(signals(panel), panel, settings)
    changed = panel.copy()
    changed.loc[changed["session"] > cutoff, ["close", "market_cap", "beta"]] = 1e100
    after = optimize_portfolio(signals(changed), changed, settings)
    assert before["target_weights"] == after["target_weights"]
    assert before["estimate_end"] == pd.Timestamp(cutoff).date().isoformat()


@pytest.mark.parametrize(("missing", "limits", "error"), [
    ("industry", {"industry_max_weights": {"tech": .5}}, "INDUSTRY_UNAVAILABLE"),
    ("market_cap", {"log_market_cap_max": 25}, "RISK_FIELD_UNAVAILABLE"),
    ("beta", {"beta_max": 1}, "RISK_FIELD_UNAVAILABLE"),
    ("volume", {"max_adv_participation": .01}, "ADV_UNAVAILABLE"),
])
def test_missing_real_exposures_fail_closed(panel, missing, limits, error):
    result = optimize_portfolio(signals(panel), panel.drop(columns=missing), config(panel, **limits))
    assert result["status"] == "blocked" and result["error_code"] == error
    assert result["target_weights"] == []


def test_impossible_turnover_and_cash_constraints_are_not_relaxed(panel):
    result = optimize_portfolio(signals(panel), panel, config(panel, cash_max=.1, max_turnover=.5))
    assert result["error_code"] == "CONSTRAINTS_INFEASIBLE"
    assert result["fallback_used"] is False


def test_signal_duplicates_do_not_double_position_and_removed_holdings_count_sales(panel):
    candidate = signals(panel)
    candidate = candidate[candidate["security_id"] != "A"]
    duplicate = pd.concat([candidate, candidate], ignore_index=True)
    settings = config(panel, previous_weights={"A": .5}, max_turnover=1.2, cash_max=.5)
    first = optimize_portfolio(candidate, panel, settings)
    second = optimize_portfolio(duplicate, panel, settings)
    assert first["target_weights"] == second["target_weights"]
    weights = {row["security_id"]: row["target_weight"] for row in first["target_weights"]}
    assert weights["A"] == 0
    assert first["turnover"] == pytest.approx(.5 + weights["B"] + weights["C"])


def test_missing_recent_history_is_not_forward_filled(panel):
    panel = panel.copy()
    panel.loc[(panel["security_id"] == "A") & (panel.index % 2 == 0), "close"] = np.nan
    result = optimize_portfolio(signals(panel), panel, config(panel))
    assert result["error_code"] == "RISK_DATA_UNAVAILABLE"


def test_parameter_selection_uses_training_estimates_and_next_open_validation(panel):
    dates = sorted(panel["session"].unique())
    settings = config(panel, training_end=pd.Timestamp(dates[35]).date(), validation_end=pd.Timestamp(dates[55]).date(),
                      risk_aversion_candidates=(0, 10, 100), cash_max=.3)
    result = optimize_portfolio(signals(panel), panel, settings)
    assert result["parameter_selection"] == "train_fit_validation_select"
    assert result["parameter_attempts"] == 3 and result["performance_observed_trials"] == 9
    assert result["validation_is_independent_final"] is False
    for trial in result["parameter_trials"]:
        assert trial["training_result"]["estimate_end"] == settings.training_end.isoformat()
        assert trial["cost_sensitivity"][0]["validation_net_return"] >= trial["validation_net_return"]
    changed = panel.copy()
    changed.loc[changed["session"] > pd.Timestamp(settings.validation_end), "raw_close"] *= 100
    repeated = optimize_portfolio(signals(changed), changed, settings)
    assert result["selected_risk_aversion"] == repeated["selected_risk_aversion"]
    assert result["parameter_trials"] == repeated["parameter_trials"]


def test_selection_dates_and_long_only_contract_are_validated(panel):
    with pytest.raises(ValidationError, match="training_end"):
        config(panel, risk_aversion_candidates=(1, 2))
    with pytest.raises(ValidationError, match="Leverage"):
        config(panel, previous_weights={"A": 1.2})


@pytest.mark.asyncio
async def test_saved_signal_requires_current_project_grant_and_matching_digest(db_session, test_user, panel, monkeypatch):
    from quantgpt.models import ResearchAuditEvent
    from quantgpt.research.contracts import (
        US_RESEARCH_FIELDS,
        DataInputRef,
        EngineIdentity,
        EvaluationConfigV1,
        EvaluationWindow,
        FactorDefinitionV1,
        ResearchScope,
        SimulationConfigV1,
    )
    from quantgpt.research.evaluations import register_evaluation, serialize_frame, write_artifact
    from quantgpt.research.portfolio import optimize_signal_reference
    from quantgpt.research.projects import ProjectAccessError, create_project

    project = await create_project(db_session, test_user.id, name="Portfolio")
    dates = sorted(panel["session"].unique())
    evaluation_config = EvaluationConfigV1(
        backend="local", direction="higher_is_better",
        scope=ResearchScope(market="us_equity", currency="USD", universe_id="fixture", universe_mode="fixed_cohort",
                            universe_version="v1", calendar_id="synthetic", calendar_version="v1",
                            timezone="America/New_York", benchmark="synthetic"),
        window=EvaluationWindow(start_session=pd.Timestamp(dates[0]).date(), end_session=pd.Timestamp(dates[-1]).date(),
                                phase="selection", split_id="fixture", split_hash="a" * 64),
        engine=EngineIdentity(engine="python", version="fixture", code_version="fixture"),
        data_inputs=(DataInputRef(source="synthetic", feed="fixture", adjustment="raw", manifest_id="fixture-snapshot",
                                 asof="2025-01-01T00:00:00Z"),),
        simulation_config=SimulationConfigV1(rebalance_anchor_session=pd.Timestamp(dates[0]).date()),
    )
    definition = FactorDefinitionV1(expression="close", fields=(US_RESEARCH_FIELDS[2],))
    row, _ = await register_evaluation(db_session, test_user.id, project.id, definition, evaluation_config)
    reference = await write_artifact(db_session, row, "signal", serialize_frame(signals(panel)))
    monkeypatch.setattr("quantgpt.research.portfolio.load_evaluation_panel", lambda *args: panel)
    with pytest.raises(ProjectAccessError, match="Snapshot"):
        await optimize_signal_reference(db_session, test_user.id, project.id, reference, config(panel), snapshot_root=".")
    db_session.add(ResearchAuditEvent(project_id=project.id, actor_id=test_user.id, action="snapshot_registered",
                                      payload={"snapshot_id": "fixture-snapshot"}))
    await db_session.flush()
    with pytest.raises(ValueError, match="REFERENCE_INTEGRITY"):
        await optimize_signal_reference(db_session, test_user.id, project.id,
                                         {**reference, "content_sha256": "b" * 64}, config(panel), snapshot_root=".")
    result = await optimize_signal_reference(db_session, test_user.id, project.id, reference, config(panel), snapshot_root=".")
    assert result["status"] == "completed" and result["artifact_ref"]["kind"] == "portfolio_optimization"
    assert result["local_strategy_eligible"] is False
    monkeypatch.setattr("quantgpt.research.portfolio.load_evaluation_panel", lambda *args: pytest.fail("cache rebuilt panel"))
    repeated = await optimize_signal_reference(db_session, test_user.id, project.id, reference, config(panel), snapshot_root=".")
    assert repeated["artifact_ref"] == result["artifact_ref"]

    from quantgpt.research.validation import observed_trial_count

    trial_payload = {"portfolio_hash": "same-immutable-optimization", "parameter_trials": [
        {"performance_observed": True}, {"performance_observed": False}, {"performance_observed": True},
    ]}
    await write_artifact(db_session, row, "portfolio_optimization", trial_payload)
    await write_artifact(db_session, row, "portfolio_optimization", {**trial_payload, "diagnostic_retry": True})
    assert await observed_trial_count(db_session, project.id) == 2
