"""Strategy diagnosis tests."""

from quantgpt.strategy.diagnosis import diagnose_strategy_metrics


def test_strategy_diagnosis_reports_taxonomy_and_spec_suggestions():
    diagnosis = diagnose_strategy_metrics(
        {"sharpe": -0.2, "annual_return": -0.1, "max_drawdown": -0.35, "turnover": 1.2},
        [{"code": "TURNOVER_LIMIT_REBALANCE_SKIPPED"}],
        [],
        {},
    )

    codes = {item["code"] for item in diagnosis["diagnoses"]}
    assert {"RETURN_WEAK", "DRAWDOWN_HIGH", "TURNOVER_HIGH", "TURNOVER_LIMIT_SKIPPED"} <= codes
    assert diagnosis["summary"] == "major"
    assert any(change["path"] == "portfolio_rule.rebalance_period" for change in diagnosis["suggested_spec_changes"])
