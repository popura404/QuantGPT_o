"""Cycle 2 StrategySpec OOS validation tests."""

import json

import pandas as pd
import pytest
from pydantic import ValidationError

from quantgpt.strategy.backtest import StrategyBacktestRequest, run_strategy_backtest
from quantgpt.strategy.service import score_strategy_payload, strategy_result_to_payload
from quantgpt.strategy.spec import StrategySpecV1, example_strategy_spec_v1
from quantgpt.strategy.validator import validate_strategy_spec
from quantgpt.validation.oos_score import compute_oos_score, compute_oos_selection_score, withhold_final_test


def _market_df(days: int = 90) -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-02", periods=days)
    stocks = ["A", "B", "C", "D", "E", "F"]
    rows = []
    prices = {stock: 10.0 + idx * 5 for idx, stock in enumerate(stocks)}
    for date in dates:
        for idx, stock in enumerate(stocks):
            daily_ret = 0.006 if idx >= 3 else -0.001
            open_price = prices[stock]
            close = open_price * (1 + daily_ret)
            rows.append({
                "trade_date": date,
                "stock_code": stock,
                "open": open_price,
                "high": max(open_price, close) * 1.01,
                "low": min(open_price, close) * 0.99,
                "close": close,
                "volume": 1_000 + idx,
                "amount": close * (1_000 + idx),
                "pct_change": daily_ret * 100,
            })
            prices[stock] = close
    return pd.DataFrame(rows)


def _oos_spec() -> dict:
    spec = example_strategy_spec_v1()
    spec["universe"] = "small_scale"
    spec["factors"] = [
        {"id": "price_rank", "expression": "close", "direction": "higher_is_better", "weight": 1.0},
    ]
    spec["signal_rules"] = {"type": "rank_threshold", "top_n": 2}
    spec["portfolio_rule"] = {"weighting": "equal_weight", "rebalance_period": 5}
    spec["risk_rules"]["max_asset_weight"] = 1.0
    spec["risk_rules"]["max_turnover"] = None
    spec["cost_model"]["bps"] = 0
    spec["validation"]["run_strategy_anti_overfit"] = False
    spec["validation"]["run_strategy_rolling_validation"] = False
    spec["validation"]["oos"] = {
        "enabled": True,
        "method": "date_ratio",
        "train_ratio": 0.5,
        "valid_ratio": 0.25,
        "test_ratio": 0.25,
        "min_train_days": 5,
        "min_valid_days": 5,
        "min_test_days": 5,
        "warmup_days": 0,
        "direction_policy": "train_fixed",
    }
    spec["validation"]["data_quality"] = {
        "enabled": True,
        "mode": "filter",
        "adjustment": "qfq",
        "max_abs_daily_ret": 0.25,
        "max_missing_ratio_per_stock": 0.2,
    }
    return spec


def test_strategy_spec_v1_accepts_oos_validation_config():
    spec = StrategySpecV1.model_validate(_oos_spec())

    assert spec.validation.oos is not None
    assert spec.validation.oos.enabled is True
    assert spec.validation.oos.direction_policy == "train_fixed"
    assert spec.validation.data_quality is not None
    assert spec.validation.data_quality.adjustment == "qfq"


def test_strategy_spec_v1_rejects_invalid_oos_ratio_and_live_fields():
    invalid_ratio = _oos_spec()
    invalid_ratio["validation"]["oos"]["test_ratio"] = 0.3
    with pytest.raises(ValidationError):
        StrategySpecV1.model_validate(invalid_ratio)

    non_bool_enabled = _oos_spec()
    non_bool_enabled["validation"]["oos"]["enabled"] = "true"
    with pytest.raises(ValidationError):
        StrategySpecV1.model_validate(non_bool_enabled)

    live_field = _oos_spec()
    live_field["validation"]["broker"] = "not_allowed"
    with pytest.raises(ValidationError):
        StrategySpecV1.model_validate(live_field)


def test_strategy_validator_accepts_oos_and_rejects_bad_data_quality_range():
    valid = validate_strategy_spec(_oos_spec())
    invalid = _oos_spec()
    invalid["validation"]["data_quality"]["max_abs_daily_ret"] = 0.01
    invalid_result = validate_strategy_spec(invalid)

    assert valid.is_valid is True
    assert invalid_result.is_valid is False


def test_strategy_backtest_returns_oos_result_and_json_safe_payload():
    result = run_strategy_backtest(
        StrategyBacktestRequest.model_validate({
            "spec": _oos_spec(),
            "start_date": "2024-01-02",
            "end_date": "2024-05-31",
            "rebalance_anchor": "2024-01-02",
        }),
        market_df=_market_df(),
    )
    payload = strategy_result_to_payload(result)

    assert result.validation_mode == "train_valid_test"
    assert payload["direction_policy"] == "train_fixed"
    assert payload["data_quality"]["enabled"] is True
    assert payload["oos_result"]["train"]["period"]
    assert payload["oos_result"]["test"]["metrics"]["turnover_source"] == "turnover_by_rebalance_eval_window"
    assert payload["oos_summary"]["decision"] in {"candidate", "watchlist", "reject"}
    assert payload["oos_score"]["metrics_scope"] == "oos_train_valid_test"
    json.dumps(payload)


def test_strategy_oos_top_level_series_are_clipped_to_test_window():
    spec = _oos_spec()
    spec["validation"]["oos"]["warmup_days"] = 5
    result = run_strategy_backtest(
        StrategyBacktestRequest.model_validate({
            "spec": spec,
            "start_date": "2024-01-02",
            "end_date": "2024-05-31",
            "rebalance_anchor": "2024-01-02",
        }),
        market_df=_market_df(),
    )

    test_start, test_end = result.oos_result["test"]["period"]
    assert result.strategy_returns.index.min() >= pd.Timestamp(test_start)
    assert result.strategy_returns.index.max() <= pd.Timestamp(test_end)
    assert pd.to_datetime(result.target_weights["trade_date"]).min() >= pd.Timestamp(test_start)
    assert result.metrics == result.oos_result["test"]["metrics"]


def test_oos_score_prioritizes_test_metrics_and_service_payload():
    oos = {
        "train": {"metrics": {"sharpe": 2.0, "ic_mean": 0.08, "max_drawdown": -0.02, "turnover": 0.2}},
        "valid": {"metrics": {"sharpe": 1.0, "ic_mean": 0.04, "max_drawdown": -0.04, "turnover": 0.2}},
        "test": {"metrics": {"sharpe": 0.2, "ic_mean": 0.03, "max_drawdown": -0.05, "turnover": 0.2}},
        "decay": {"test_sharpe_decay": 0.9, "test_ic_decay": 0.5},
    }
    score = compute_oos_score(oos)
    payload_score = score_strategy_payload({"oos_result": oos})

    assert score["decision"] == "reject"
    assert score["overfit_risk"] == "high"
    assert payload_score["metrics_scope"] == "oos_train_valid_test"


def test_oos_selection_score_ignores_and_withholds_test_metrics():
    oos = {
        "train": {"metrics": {"sharpe": 1.5, "ic_mean": 0.06, "max_drawdown": -0.02, "turnover": 0.2}},
        "valid": {"metrics": {"sharpe": 0.9, "ic_mean": 0.04, "max_drawdown": -0.04, "turnover": 0.2}},
        "test": {"metrics": {"sharpe": -2.0, "ic_mean": -0.1, "max_drawdown": -0.2, "turnover": 0.2}},
        "decay": {
            "valid_sharpe_decay": 0.4,
            "valid_ic_decay": 0.33,
            "test_sharpe_decay": 3.0,
            "test_ic_decay": 3.0,
        },
    }
    withheld = withhold_final_test(oos)
    score = compute_oos_selection_score(withheld)

    assert withheld["test"]["status"] == "withheld"
    assert withheld["test"]["metrics"] == {}
    assert score["metrics_scope"] == "oos_train_valid_selection"
    assert score["decision"] != "reject"
    assert "test_score" not in score
