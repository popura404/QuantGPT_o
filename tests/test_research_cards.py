"""Card evidence is descriptive, scoped and derived from actual numerical data."""

from copy import deepcopy

import numpy as np
import pandas as pd
import pytest

from quantgpt.backtest import api_context, run_factor_backtest
from quantgpt.research.cards import (
    EvaluationEvidence,
    build_factor_research_card,
    compare_evaluation_evidence,
    compare_registered_variants,
)
from quantgpt.research.contracts import (
    US_RESEARCH_FIELDS,
    DataInputRef,
    EngineIdentity,
    EvaluationConfigV1,
    EvaluationWindow,
    FactorDefinitionV1,
    ResearchScope,
    SimulationConfigV1,
    canonical_json,
)


def evidence(expression="rank(close)", *, horizon=1, fees=2., scope="selection"):
    dates = pd.bdate_range("2024-01-02", periods=40)
    definition = FactorDefinitionV1(expression=expression, fields=(next(f for f in US_RESEARCH_FIELDS if f.name == "close"),))
    config = EvaluationConfigV1(backend="local", direction="higher_is_better",
        scope=ResearchScope(market="us_equity", currency="USD", universe_id="synthetic", universe_mode="fixed_cohort",
                            universe_version="fixture1", calendar_id="synthetic", calendar_version="fixture1",
                            timezone="America/New_York", benchmark="none"),
        window=EvaluationWindow(start_session=dates[0].date(), end_session=dates[-1].date(), phase=scope,
                                split_id="fixture", split_hash="a" * 64, label_horizon_sessions=horizon, purge_sessions=horizon),
        data_inputs=(DataInputRef(source="synthetic", feed="fixture", adjustment="raw", asof="2024-01-01T00:00:00Z"),),
        simulation_config=SimulationConfigV1(rebalance_anchor_session=dates[0].date(), fees_bps=fees,
                                             rebalance_every_sessions=horizon),
        engine=EngineIdentity(engine="python", version="test", code_version="fixture"))
    output = {"semantics_version": config.engine.semantics_version,
              "simulation_config": config.simulation_config.model_dump(mode="json"), "holding_period": horizon,
              "strategy_returns": pd.Series(np.sin(np.arange(40)) * .01, index=dates),
              "_raw_ic_series": pd.Series(np.tile([.1, -.1], 20), index=dates),
              "_raw_rank_ic_series": pd.Series(np.tile([.1, -.1], 20), index=dates),
              "_factor_df": pd.DataFrame([{"trade_date": day, "stock_code": code, "factor_value": value}
                  for day in dates for code, value in (("A", 1.), ("B", 2.), ("C", 3.))])}
    return EvaluationEvidence(f"fixture-{expression}-{horizon}-{fees}", definition, config, output)


def card(item):
    return build_factor_research_card(evaluation_id=item.evaluation_id, definition=item.definition,
                                      config=item.config, output=item.output, trial_count=4)


def test_card_deterministic_identity_and_explicit_missing_evidence():
    item = evidence()
    result = card(item)
    assert result == card(item)
    assert result["evaluation_hash"] == item.identity
    assert result["conclusion"] == "not_significant_at_pointwise_95_percent"
    assert result["statistics"]["observed_project_trials"] == 4
    assert result["research_decision"] == "not_evaluated"
    assert result["risk_exposures"]["status"] == "unavailable"
    assert result["incremental_contribution"]["status"] == "unavailable"
    assert "score" not in result
    canonical_json(result)


def test_empty_data_is_insufficient_not_zero_performance_success():
    item = evidence()
    result = card(EvaluationEvidence(item.evaluation_id, item.definition, item.config, {}))
    assert result["conclusion"] == "insufficient_data"
    assert result["ic"]["rank_ic"]["mean"] is None
    assert result["performance"]["status"] == "unavailable"
    canonical_json(result)


@pytest.mark.parametrize("key,value,match", [
    ("holding_period", 5, "horizon"), ("simulation_config", {}, "simulation"),
    ("semantics_version", "old", "semantics"),
])
def test_card_rejects_output_from_another_contract(key, value, match):
    item = evidence()
    item.output[key] = value
    with pytest.raises(ValueError, match=match):
        card(item)


def test_card_rejects_return_observations_outside_registered_window():
    item = evidence()
    item.output["strategy_returns"].loc[pd.Timestamp("2025-01-02")] = .99
    with pytest.raises(ValueError, match="outside"):
        card(item)


def test_numerical_similarity_detects_different_strings_and_keeps_sign():
    baseline, candidate = evidence(), evidence("-rank(close)")
    candidate.output["_factor_df"]["factor_value"] *= -1
    candidate.output["strategy_returns"] += .001
    report = compare_evaluation_evidence(candidate, baseline, registered_baseline_hash=baseline.identity)
    assert all(row["rank_correlation"] == -1 for row in report["signal_similarity"])
    assert report["return_correlation"] == pytest.approx(1.)
    assert report["paired_daily_net_return_difference"]["mean"] == pytest.approx(.001)
    assert report["selection_adjusted"] is False
    canonical_json(report)


@pytest.mark.parametrize("change", ["cost", "split", "data"])
def test_incremental_comparison_rejects_nonmatching_research_context(change):
    baseline, candidate = evidence(), evidence("-rank(close)")
    payload = candidate.config.model_dump(mode="json")
    if change == "cost":
        payload["simulation_config"]["fees_bps"] += 1
    elif change == "split":
        payload["window"]["split_hash"] = "b" * 64
    else:
        payload["data_inputs"][0]["source"] = "another_vendor"
    config = EvaluationConfigV1.model_validate(payload)
    output = deepcopy(candidate.output)
    output["simulation_config"] = config.simulation_config.model_dump(mode="json")
    changed = EvaluationEvidence(candidate.evaluation_id, candidate.definition, config, output)
    with pytest.raises(ValueError, match="same data"):
        compare_evaluation_evidence(changed, baseline, registered_baseline_hash=baseline.identity)


def test_incremental_comparison_requires_frozen_hash_and_complete_alignment():
    baseline, candidate = evidence(), evidence("-rank(close)")
    with pytest.raises(ValueError, match="baseline identity"):
        compare_evaluation_evidence(candidate, baseline, registered_baseline_hash="b" * 64)
    candidate.output["strategy_returns"] = candidate.output["strategy_returns"].iloc[1:]
    with pytest.raises(ValueError, match="matching return sessions"):
        compare_evaluation_evidence(candidate, baseline, registered_baseline_hash=baseline.identity)


def test_horizon_and_cost_variants_keep_each_trial_identity():
    horizons = compare_registered_variants([evidence(horizon=1), evidence(horizon=5)], variation="horizon")
    assert horizons["observed_trials_in_this_comparison"] == 2
    assert [row["horizon_sessions"] for row in horizons["evaluations"]] == [1, 5]
    costs = compare_registered_variants([evidence(fees=1), evidence(fees=10)], variation="cost")
    assert len({row["evaluation_hash"] for row in costs["evaluations"]}) == 2
    assert costs["selection_adjusted"] is False
    with pytest.raises(ValueError, match="Duplicate"):
        compare_registered_variants([evidence(), evidence()], variation="cost")


def test_real_backtest_output_produces_gross_net_ledger_card():
    item = evidence()
    frame = pd.DataFrame([
        {"trade_date": day, "stock_code": f"S{index}", "open": 100. + index + offset,
         "close": 101. + index + offset, "high": 101. + index + offset, "low": 100. + index + offset,
         "volume": 100., "amount": 10000.}
        for offset, day in enumerate(item.output["strategy_returns"].index) for index in range(15)])
    frame.attrs.update(adjustment="raw", calendar_sessions=frame.trade_date.unique().tolist(), corporate_actions=[])
    with api_context():
        output = run_factor_backtest(frame, expression=item.definition.expression, holding_period=1,
            direction_mode="fixed", fixed_direction=1, simulation_config=item.config.simulation_config,
            neutralize_industry=False, neutralize_cap=False,
            evaluation_start=item.config.window.start_session.isoformat(),
            evaluation_end=item.config.window.end_session.isoformat())
    result = card(EvaluationEvidence(item.evaluation_id, item.definition, item.config, output))
    assert len(result["groups"]) == 5
    assert result["groups"][0]["gross"]["total_return"] >= result["groups"][0]["net"]["total_return"]
    assert result["groups"][0]["charged_fees"] > 0
    assert result["execution"]["status"] == "simulated"
    canonical_json(result)
