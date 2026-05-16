"""Strategy score tests."""

from quantgpt.strategy.backtest import StrategyBacktestRequest, run_strategy_backtest
from quantgpt.strategy.score import compute_strategy_score
from quantgpt.strategy.spec import example_strategy_spec

from .test_strategy_backtest import _market_df


def test_strategy_score_uses_strategy_result_contract():
    spec = example_strategy_spec()
    spec["universe"] = "small_scale"
    spec["factors"][0]["expression"] = "close"
    spec["signal_rules"]["long_quantile"] = 0.5
    spec["risk_rules"]["max_asset_weight"] = 1.0
    spec["risk_rules"]["max_turnover"] = None
    spec["cost_model"]["bps"] = 0
    result = run_strategy_backtest(
        StrategyBacktestRequest.model_validate({
            "spec": spec,
            "start_date": "2024-01-02",
            "end_date": "2024-02-02",
        }),
        market_df=_market_df(),
    )

    score = compute_strategy_score(result)

    assert 0 <= score["score"] <= 100
    assert score["grade"] in {"A", "B", "C", "D"}
    assert "risk_logs" in score
    assert score["strategy_anti_overfit"] == "not_run"
