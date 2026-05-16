"""Strategy-level validation tests."""

import pandas as pd

from quantgpt.strategy.result import StrategyBacktestResult
from quantgpt.strategy.spec import StrategySpecV1, example_strategy_spec_v1
from quantgpt.strategy.validation import run_strategy_anti_overfit, run_strategy_rolling_validation


def _result():
    return StrategyBacktestResult(
        spec=StrategySpecV1.model_validate(example_strategy_spec_v1()),
        start_date="2024-01-02",
        end_date="2024-01-12",
        benchmark="hs300",
        strategy_returns=pd.Series(
            [0.01, -0.002, 0.004, 0.006, 0.003, -0.001],
            index=pd.bdate_range("2024-01-03", periods=6),
            name="strategy",
        ),
        target_weights=pd.DataFrame(),
        cash_weights=pd.DataFrame(),
        turnover_by_rebalance=pd.DataFrame(),
        cost_by_rebalance=pd.DataFrame(),
        risk_logs=[],
        latest_holdings=[],
        metrics={"max_drawdown": -0.02, "turnover": 0.3},
        validation_issues=[],
        diagnostics={},
    )


def test_strategy_anti_overfit_returns_structured_summary():
    summary = run_strategy_anti_overfit(_result())

    assert summary["type"] == "strategy_anti_overfit"
    assert summary["passed"] is True
    assert summary["passed_count"] >= 3


def test_strategy_rolling_validation_returns_windows():
    summary = run_strategy_rolling_validation(_result(), windows=3)

    assert summary["type"] == "strategy_rolling_validation"
    assert summary["total_windows"] == 3
    assert len(summary["windows"]) == 3
