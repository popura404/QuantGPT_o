"""Post-MVP demo market adapter tests."""

from quantgpt.strategy.adapters import get_adapter, list_markets
from quantgpt.strategy.backtest import StrategyBacktestRequest, run_strategy_backtest
from quantgpt.strategy.spec import example_strategy_spec_v1
from quantgpt.strategy.validator import validate_strategy_spec


def _demo_spec():
    spec = example_strategy_spec_v1()
    spec["market"] = "demo_global_equity"
    spec["universe"] = "demo_small"
    spec["factors"] = [
        {"id": "demo_momentum", "expression": "close", "direction": "higher_is_better", "weight": 1.0}
    ]
    spec["signal_rules"] = {"type": "rank_threshold", "top_n": 2}
    spec["portfolio_rule"] = {"weighting": "equal_weight", "rebalance_period": 5}
    spec["risk_rules"]["max_asset_weight"] = 1.0
    spec["risk_rules"]["max_turnover"] = None
    spec["validation"]["run_strategy_anti_overfit"] = False
    spec["validation"]["run_strategy_rolling_validation"] = False
    return spec


def test_demo_global_equity_adapter_is_discoverable_and_validates():
    markets = {market["market"] for market in list_markets()}
    result = validate_strategy_spec(_demo_spec())

    assert "demo_global_equity" in markets
    assert result.is_valid is True


def test_demo_global_equity_backtest_runs_without_external_data():
    adapter = get_adapter("demo_global_equity")
    request = StrategyBacktestRequest.model_validate({
        "spec": _demo_spec(),
        "start_date": "2024-01-02",
        "end_date": "2024-02-15",
        "benchmark": "demo_world",
    })

    result = run_strategy_backtest(request, *adapter.fetch_market_data("demo_small", "2024-01-02", "2024-02-15"))

    assert not result.strategy_returns.empty
    assert set(result.target_weights["stock_code"]).issubset({"DG.A", "DG.B", "DG.C", "DG.D"})
