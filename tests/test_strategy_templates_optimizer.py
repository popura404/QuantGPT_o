"""Strategy template and optimizer tests."""

from quantgpt.strategy.optimizer import optimize_candidate_weights
from quantgpt.strategy.spec import StrategySpecV1
from quantgpt.strategy.templates import instantiate_strategy_template, list_strategy_templates
from quantgpt.strategy.validator import validate_strategy_spec


def test_strategy_templates_instantiate_to_valid_specs():
    templates = list_strategy_templates()
    spec = instantiate_strategy_template("momentum_top_n_v1", {"signal_rules.top_n": 5})
    result = validate_strategy_spec(spec)

    assert any(item["id"] == "momentum_top_n_v1" for item in templates)
    assert spec["signal_rules"]["top_n"] == 5
    assert result.is_valid is True


def test_optimizer_respects_risk_applier_max_asset_weight():
    spec = StrategySpecV1.model_validate(instantiate_strategy_template("momentum_top_n_v1", {"risk_rules.max_asset_weight": 0.4}))
    optimized = optimize_candidate_weights(
        [
            {"trade_date": "2024-01-02", "stock_code": "A", "score": 10.0},
            {"trade_date": "2024-01-02", "stock_code": "B", "score": 1.0},
        ],
        spec,
    )

    assert max(row["target_weight"] for row in optimized["target_weights"]) <= 0.4
    assert optimized["cash_weights"][0]["cash_weight"] > 0
    assert any(log["code"] == "MAX_ASSET_WEIGHT_CLIPPED" for log in optimized["risk_logs"])
