"""Strategy signal export tests."""

from quantgpt.strategy.export import FORBIDDEN_EXPORT_KEYS
from quantgpt.strategy.service import export_strategy_candidate_payload
from quantgpt.strategy.spec import example_strategy_spec_v1
from quantgpt.validation.promotion import build_factor_validation_provenance


def _validation_provenance() -> dict:
    return build_factor_validation_provenance(
        oos_result={
            "direction_policy": "train_fixed",
            "train": {"metrics": {"long_short_sharpe": 1.0, "direction_adjusted_rank_ic_mean": 0.03}},
            "valid": {"metrics": {"long_short_sharpe": 0.9, "direction_adjusted_rank_ic_mean": 0.02}},
            "test": {"metrics": {"long_short_sharpe": 0.8, "direction_adjusted_rank_ic_mean": 0.02}},
        },
        oos_score={"decision": "candidate", "score": 80, "grade": "A", "overfit_risk": "low"},
        data_quality={"enabled": True, "after_rows": 100, "after_stocks": 10},
        rolling_validation={"score": 70, "windows": [{"window_index": 0}]},
        placebo_test={"passed": True, "details": {"perm_pass": True, "decay_ok": True, "shift_ics": {"5": 0.01}}},
    )


def _result_payload():
    return {
        "spec": example_strategy_spec_v1(),
        "start_date": "2024-01-02",
        "end_date": "2024-02-02",
        "benchmark": "hs300",
        "metrics": {"sharpe": 1.0, "annual_return": 0.1, "max_drawdown": -0.05, "turnover": 0.2},
        "risk_logs": [],
        "validation_issues": [],
        "diagnostics": {},
        "latest_holdings": [],
        "strategy_returns": [{"date": "2024-01-03", "value": 0.01}],
        "target_weights": [
            {"trade_date": "2024-01-02", "stock_code": "A", "target_weight": 0.6},
            {"trade_date": "2024-01-02", "stock_code": "B", "target_weight": 0.4},
        ],
        "cash_weights": [{"trade_date": "2024-01-02", "cash_weight": 0.0}],
        "turnover_by_rebalance": [],
        "cost_by_rebalance": [],
        "validation_provenance": _validation_provenance(),
    }


def _walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def test_export_strategy_candidate_has_review_signal_schema_without_execution_fields():
    payload = export_strategy_candidate_payload(_result_payload())

    assert payload["spec_version"] == "strategy_spec/v1"
    assert len(payload["signals"]) == 2
    assert payload["signals"][0]["action_hint"] == "buy"
    assert payload["validation_provenance"]["promotion_state"] == "promotion_ready"
    assert all(not (FORBIDDEN_EXPORT_KEYS & set(item)) for item in _walk(payload))


def test_export_strategy_candidate_rejects_missing_validation_provenance():
    result = _result_payload()
    result.pop("validation_provenance")

    try:
        export_strategy_candidate_payload(result)
    except ValueError as exc:
        assert "VALIDATION_PROVENANCE_REQUIRED" in str(exc)
    else:
        raise AssertionError("export should require validation provenance")
