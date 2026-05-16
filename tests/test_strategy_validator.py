"""Strategy validator tests."""

from quantgpt.strategy.errors import (
    DATA_FIELD_UNSUPPORTED,
    EXPRESSION_INVALID,
    MARKET_UNSUPPORTED,
    RISK_SHORT_NOT_ALLOWED,
    RULE_UNSUPPORTED,
    SCHEMA_UNKNOWN_FIELD,
)
from quantgpt.strategy.spec import example_strategy_spec
from quantgpt.strategy.validator import validate_strategy_spec


def _codes(result):
    return {issue.code for issue in result.issues}


def test_valid_strategy_spec_validates():
    result = validate_strategy_spec(example_strategy_spec())

    assert result.is_valid is True
    assert result.issues == []


def test_unknown_field_returns_schema_unknown_field():
    data = example_strategy_spec()
    data["unknown"] = "value"

    result = validate_strategy_spec(data)

    assert result.is_valid is False
    assert SCHEMA_UNKNOWN_FIELD in _codes(result)


def test_unknown_market_returns_market_unsupported():
    data = example_strategy_spec()
    data["market"] = "crypto"

    result = validate_strategy_spec(data)

    assert result.is_valid is False
    assert MARKET_UNSUPPORTED in _codes(result)


def test_shorting_returns_risk_short_not_allowed():
    data = example_strategy_spec()
    data["risk_rules"]["allow_short"] = True

    result = validate_strategy_spec(data)

    assert result.is_valid is False
    assert RISK_SHORT_NOT_ALLOWED in _codes(result)


def test_invalid_expression_returns_expression_invalid():
    data = example_strategy_spec()
    data["factors"][0]["expression"] = "rank(close"

    result = validate_strategy_spec(data)

    assert result.is_valid is False
    assert EXPRESSION_INVALID in _codes(result)


def test_unknown_data_field_returns_data_field_unsupported():
    data = example_strategy_spec()
    data["factors"][0]["expression"] = "rank(not_a_field)"

    result = validate_strategy_spec(data)

    assert result.is_valid is False
    assert DATA_FIELD_UNSUPPORTED in _codes(result)


def test_post_mvp_validation_flag_returns_rule_unsupported():
    data = example_strategy_spec()
    data["validation"]["run_strategy_rolling_validation"] = True

    result = validate_strategy_spec(data)

    assert result.is_valid is False
    assert RULE_UNSUPPORTED in _codes(result)
