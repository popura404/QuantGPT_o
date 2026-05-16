"""Post-MVP top-N strategy tests."""

import pandas as pd
import pytest

from quantgpt.strategy.portfolio import build_strategy_portfolio
from quantgpt.strategy.signals import build_rank_threshold_signals
from quantgpt.strategy.spec import StrategySpecV1, example_strategy_spec_v1


def _spec(weighting="equal_weight"):
    data = example_strategy_spec_v1()
    data["signal_rules"] = {"type": "rank_threshold", "top_n": 2}
    data["portfolio_rule"]["weighting"] = weighting
    data["risk_rules"]["max_asset_weight"] = 1.0
    return StrategySpecV1.model_validate(data)


def _factor_frame():
    return pd.DataFrame({
        "trade_date": pd.to_datetime(["2024-01-02"] * 5),
        "stock_code": ["A", "B", "C", "D", "E"],
        "factor_value": [0.1, 0.9, 0.4, 0.8, 0.2],
    })


def test_top_n_selects_exact_asset_count():
    signals = build_rank_threshold_signals(_factor_frame(), _spec())

    selected = signals[signals["eligibility"]]
    assert len(selected) == 2
    assert set(selected["stock_code"]) == {"B", "D"}


def test_score_weighted_portfolio_sums_to_one():
    signals = build_rank_threshold_signals(_factor_frame(), _spec("score_weighted"))
    weights = build_strategy_portfolio(signals, _spec("score_weighted"))

    assert weights["target_weight"].sum() == pytest.approx(1.0)
    assert weights["target_weight"].nunique() == 2
