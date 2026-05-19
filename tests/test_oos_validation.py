"""Out-of-sample validation regressions."""

import json

import pandas as pd
import pytest

from quantgpt.validation.oos_backtest import run_factor_oos_backtest, to_public_oos_result
from quantgpt.validation.split import OOSConfig, infer_warmup_days, split_by_dates


@pytest.fixture
def oos_market_df():
    dates = pd.bdate_range("2024-01-02", periods=90)
    stocks = [f"S{i:02d}" for i in range(12)]
    prices = {stock: (10.0 + i if i < 6 else 100.0 + i) for i, stock in enumerate(stocks)}
    rows = []
    for date in dates:
        for i, stock in enumerate(stocks):
            ret = 0.01 if i < 6 else -0.003
            prev = prices[stock]
            close = prev * (1 + ret)
            rows.append({
                "trade_date": date,
                "stock_code": stock,
                "open": prev,
                "high": max(prev, close) * 1.01,
                "low": min(prev, close) * 0.99,
                "close": close,
                "volume": 1_000_000 + i,
                "amount": close * (1_000_000 + i),
                "pct_change": ret * 100,
            })
            prices[stock] = close
    return pd.DataFrame(rows)


def _small_config(**kwargs) -> OOSConfig:
    params = {
        "train_ratio": 0.5,
        "valid_ratio": 0.25,
        "test_ratio": 0.25,
        "min_train_days": 5,
        "min_valid_days": 5,
        "min_test_days": 5,
        "warmup_days": 3,
    }
    params.update(kwargs)
    return OOSConfig(**params)


def test_date_ratio_split_has_no_overlap_and_warmup_masks(oos_market_df):
    split = split_by_dates(oos_market_df, _small_config(), expression="ts_mean(close, 3)", holding_period=5)

    train_dates = set(split["frames"]["train"].loc[split["eval_masks"]["train"], "trade_date"])
    valid_dates = set(split["frames"]["valid"].loc[split["eval_masks"]["valid"], "trade_date"])
    test_dates = set(split["frames"]["test"].loc[split["eval_masks"]["test"], "trade_date"])

    assert train_dates.isdisjoint(valid_dates)
    assert valid_dates.isdisjoint(test_dates)
    assert len(split["frames"]["valid"]) > int(split["eval_masks"]["valid"].sum())
    assert split["resolved_warmup_days"] == 3
    assert split["rebalance_anchor"] == "2024-01-02"


def test_date_cut_boundaries_are_inclusive(oos_market_df):
    dates = sorted(pd.to_datetime(oos_market_df["trade_date"].unique()))
    split = split_by_dates(
        oos_market_df,
        _small_config(method="date_cut", train_end=str(dates[29].date()), valid_end=str(dates[59].date())),
        expression="close",
        holding_period=5,
    )

    assert split["eval_windows"]["train"]["end"] == str(dates[29].date())
    assert split["eval_windows"]["valid"]["start"] == str(dates[30].date())
    assert split["eval_windows"]["valid"]["end"] == str(dates[59].date())
    assert split["eval_windows"]["test"]["start"] == str(dates[60].date())


def test_split_requires_warmup_inputs(oos_market_df):
    with pytest.raises(ValueError, match="expression and holding_period"):
        split_by_dates(oos_market_df, OOSConfig(min_train_days=5, min_valid_days=5, min_test_days=5))


def test_resolved_warmup_takes_precedence(oos_market_df):
    split = split_by_dates(
        oos_market_df,
        _small_config(warmup_days=20),
        expression="ts_mean(close, 30)",
        holding_period=5,
        resolved_warmup_days=2,
    )

    assert split["resolved_warmup_days"] == 2


def test_warmup_inference_covers_aliases_and_fallback():
    warmup, warnings = infer_warmup_days("sma(close, 7) + adv20 + boll_upper(close, 12)", 5)
    fallback, fallback_warnings = infer_warmup_days("obv(close, volume)", 5)

    assert warmup == 20
    assert warnings == []
    assert fallback == 252
    assert fallback_warnings


def test_run_factor_oos_backtest_uses_train_fixed_direction_and_public_payload(oos_market_df):
    result = run_factor_oos_backtest(
        oos_market_df,
        "close",
        n_groups=3,
        holding_period=5,
        neutralize_industry=False,
        neutralize_cap=False,
        oos_config=_small_config(warmup_days=0),
        rebalance_anchor="2024-01-02",
    )

    oos = result["oos_result"]
    assert oos["direction_policy"] == "train_fixed"
    assert oos["direction_basis"] == "cost_adjusted_group_mean"
    assert oos["fixed_direction"] == -1
    assert oos["valid"]["metrics"]["direction_adjusted_rank_ic_mean"] > 0
    assert oos["test"]["metrics"]["turnover_source"] == "selected_group_holdings_eval_mask"
    assert "valid_sharpe_decay" in oos["decay"]

    public = to_public_oos_result(oos)
    json.dumps(public)
    assert "_train_result" not in public
    assert public["report_scope"] == "oos_train_valid_test"


def test_oos_rejects_external_direction_override(oos_market_df):
    with pytest.raises(ValueError, match="train_fixed"):
        run_factor_oos_backtest(
            oos_market_df,
            "close",
            n_groups=3,
            holding_period=5,
            neutralize_industry=False,
            neutralize_cap=False,
            oos_config=_small_config(warmup_days=0),
            direction_mode="fixed",
            fixed_direction=1,
        )
