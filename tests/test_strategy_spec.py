"""StrategySpec v0 contract tests."""

import pytest
from pydantic import ValidationError

from quantgpt.strategy.spec import StrategySpecV0, StrategySpecV1, example_strategy_spec, example_strategy_spec_v1


def test_valid_example_strategy_spec_passes():
    spec = StrategySpecV0.model_validate(example_strategy_spec())

    assert spec.schema_version == "strategy_spec/v0"
    assert spec.factors[0].direction == "higher_is_better"
    assert spec.portfolio_rule.weighting == "equal_weight"


def test_unknown_field_is_rejected():
    data = example_strategy_spec()
    data["python_code"] = "print('do not run')"

    with pytest.raises(ValidationError):
        StrategySpecV0.model_validate(data)


def test_missing_direction_is_rejected():
    data = example_strategy_spec()
    data["factors"][0].pop("direction")

    with pytest.raises(ValidationError):
        StrategySpecV0.model_validate(data)


def test_multiple_factors_are_rejected():
    data = example_strategy_spec()
    data["factors"].append(dict(data["factors"][0], id="second_factor"))

    with pytest.raises(ValidationError):
        StrategySpecV0.model_validate(data)


def test_non_a_share_market_is_rejected():
    data = example_strategy_spec()
    data["market"] = "us_equity"

    with pytest.raises(ValidationError):
        StrategySpecV0.model_validate(data)


def test_post_mvp_values_are_rejected():
    data = example_strategy_spec()
    data["portfolio_rule"]["weighting"] = "weighted"
    with pytest.raises(ValidationError):
        StrategySpecV0.model_validate(data)


def test_strategy_spec_v1_accepts_multifactor_top_n_and_exports():
    spec = StrategySpecV1.model_validate(example_strategy_spec_v1())

    assert spec.schema_version == "strategy_spec/v1"
    assert len(spec.factors) == 2
    assert spec.signal_rules.top_n == 20
    assert spec.portfolio_rule.weighting == "score_weighted"
    assert spec.outputs.signal_export is True


def test_strategy_spec_v1_still_rejects_execution_fields():
    data = example_strategy_spec_v1()
    data["broker"] = "not_allowed"

    with pytest.raises(ValidationError):
        StrategySpecV1.model_validate(data)

    data = example_strategy_spec()
    data["outputs"]["signal_export"] = True
    with pytest.raises(ValidationError):
        StrategySpecV0.model_validate(data)
