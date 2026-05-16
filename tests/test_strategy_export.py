"""Strategy signal export tests."""

from quantgpt.strategy.export import FORBIDDEN_EXPORT_KEYS
from quantgpt.strategy.service import export_strategy_candidate_payload
from quantgpt.strategy.spec import example_strategy_spec_v1


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
    assert all(not (FORBIDDEN_EXPORT_KEYS & set(item)) for item in _walk(payload))
