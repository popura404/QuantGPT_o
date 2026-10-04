"""Regressions exercising the real factor/strategy entrypoints and shared ledger."""

import numpy as np
import pandas as pd
import pytest

from quantgpt.backtest import (
    _calc_max_drawdown,
    api_context,
    build_rebalance_dates,
    compute_factor_values,
    run_factor_backtest,
)
from quantgpt.research.contracts import CapabilityBlockerError
from quantgpt.strategy.backtest import (
    StrategyBacktestRequest,
    _compute_strategy_factor_values,
    _run_strategy_single_pass,
    run_strategy_backtest,
)
from quantgpt.strategy.spec import example_strategy_spec_v1


def _market(days=8, stocks=12):
    return pd.DataFrame([
        {"trade_date": day, "stock_code": f"S{index:02}", "open": 100.0 + index, "close": 100.0 + index,
         "high": 100.0 + index, "low": 100.0 + index, "volume": 100.0 + index, "amount": (100.0 + index) ** 2}
        for day in pd.bdate_range("2024-01-02", periods=days) for index in range(stocks)
    ])


def _request(expression="close", period=3):
    spec = example_strategy_spec_v1()
    spec.update(universe="small_scale", factors=[{"id": "factor", "expression": expression, "weight": 1, "direction": "higher_is_better"}])
    spec["signal_rules"] = {"type": "rank_threshold", "top_n": 2}
    spec["portfolio_rule"] = {"weighting": "equal_weight", "rebalance_period": period}
    spec["risk_rules"].update(max_asset_weight=1.0, max_turnover=None)
    spec["cost_model"]["bps"] = 0
    spec["validation"].update(run_strategy_anti_overfit=False, run_strategy_rolling_validation=False, data_quality={"enabled": False})
    return StrategyBacktestRequest.model_validate({"spec": spec, "start_date": "2024-01-02", "end_date": "2024-01-31"})


def test_first_loss_is_in_max_drawdown():
    assert _calc_max_drawdown(pd.Series([-0.1, 0.01])) == pytest.approx(-0.1)


def test_anchor_uses_shared_sessions_and_never_weekday_guess():
    sessions = pd.to_datetime(["2024-01-02", "2024-01-04", "2024-01-05", "2024-01-08", "2024-01-09"])
    whole = build_rebalance_dates(sessions, 3, "2024-01-02", calendar_sessions=sessions)
    sliced = build_rebalance_dates(sessions[2:], 3, "2024-01-02", calendar_sessions=sessions)
    assert sliced == [day for day in whole if day >= sessions[2]] == [pd.Timestamp("2024-01-08")]
    with pytest.raises(ValueError, match="CALENDAR_CONTEXT_REQUIRED"):
        build_rebalance_dates(sessions[2:], 3, "2024-01-02")


def test_capability_failure_is_not_silenced_as_all_nan():
    with pytest.raises(CapabilityBlockerError):
        compute_factor_values(_market(), "cap")


def test_all_missing_or_insufficient_warmup_never_buys():
    result = run_strategy_backtest(_request("ts_mean(close, 100)"), market_df=_market())
    assert result.target_weights.empty
    assert result.latest_holdings == []
    assert result.strategy_returns.eq(0).all()


def test_signal_missingness_does_not_remove_held_valuation_rows():
    frame = _market()
    first = frame["trade_date"].min()
    frame.loc[frame["trade_date"] > first, "volume"] = np.nan
    frame.loc[frame["trade_date"] >= pd.Timestamp("2024-01-04"), "close"] *= 2
    result = run_strategy_backtest(_request("volume"), market_df=frame)
    assert len(result.latest_holdings) == 2
    assert result.metrics["total_return"] == pytest.approx(1)


def test_multifactor_composite_and_ic_do_not_depend_on_factor_order():
    frame = _market(days=8)
    frame["daily_ret"] = frame.groupby("stock_code")["close"].pct_change(fill_method=None)
    req = _request()
    data = req.spec.model_dump()
    data["factors"] = [
        {"id": "close", "expression": "close", "weight": 0.7, "direction": "higher_is_better"},
        {"id": "volume", "expression": "volume", "weight": 0.3, "direction": "lower_is_better"},
    ]
    first_spec = type(req.spec).model_validate(data)
    data["factors"].reverse()
    second_spec = type(req.spec).model_validate(data)
    first, first_ic = _compute_strategy_factor_values(frame, first_spec, neutralize_industry=False, neutralize_cap=False)
    second, second_ic = _compute_strategy_factor_values(frame, second_spec, neutralize_industry=False, neutralize_cap=False)
    np.testing.assert_allclose(first["factor_value"], second["factor_value"], equal_nan=True)
    np.testing.assert_allclose(first_ic, second_ic, equal_nan=True)
    np.testing.assert_allclose(first_ic, first["factor_value"], equal_nan=True)


def test_factor_spread_deducts_both_sleeves_fees_on_constant_prices():
    frame = _market(days=8, stocks=4)
    frame["close"] = frame["open"] = 100.0
    session_number = frame["trade_date"].rank(method="dense").astype(int)
    security_number = frame["stock_code"].str[1:].astype(int)
    factor = security_number.where(session_number % 2 == 0, 3 - security_number).astype(float)
    net_returns = []
    with api_context():
        for cost_rate in [0.0, 0.001, 0.01]:
            result = run_factor_backtest(frame, precomputed_factor=factor, n_groups=2, holding_period=1,
                                         neutralize_industry=False, neutralize_cap=False,
                                         direction_mode="fixed", fixed_direction=1, cost_rate=cost_rate)
            net_returns.append(result["ls_returns"].sum())
            if cost_rate > 0:
                assert result["ls_returns"].lt(0).all()
    assert net_returns[0] > net_returns[1] > net_returns[2]


def test_factor_group_retains_quantities_between_rebalances():
    frame = _market(days=5, stocks=4)
    frame["open"] = frame["close"] = 100.0
    day = sorted(frame["trade_date"].unique())[2]
    frame.loc[(frame["trade_date"] == day) & (frame["stock_code"] == "S03"), ["open", "close"]] = 200
    factor = frame["stock_code"].str[1:].astype(float)
    with api_context():
        result = run_factor_backtest(frame, precomputed_factor=factor, n_groups=2, holding_period=3,
                                     neutralize_industry=False, neutralize_cap=False,
                                     direction_mode="fixed", fixed_direction=1, cost_rate=0)
    assert (1 + result["strategy_returns"].iloc[:3]).prod() - 1 == pytest.approx(0)
    states = result["_group_ledgers"][1].states
    assert states.iloc[0]["positions"] == states.iloc[2]["positions"]


def test_fresh_cash_scoring_window_excludes_warmup_trades_and_matches_ordinary_run():
    frame = _market(days=12)
    frame["daily_ret"] = frame.groupby("stock_code")["close"].pct_change(fill_method=None)
    frame.attrs["calendar_sessions"] = sorted(frame["trade_date"].unique())
    req = _request("ts_mean(close, 3)", period=2)
    req = req.model_copy(update={"rebalance_anchor": "2024-01-02"})
    window = {"start": "2024-01-10", "end": "2024-01-17"}
    sliced = _run_strategy_single_pass(req, frame, window)
    ordinary = _run_strategy_single_pass(req.model_copy(update={"start_date": window["start"], "end_date": window["end"]}), frame)
    pd.testing.assert_series_equal(sliced.strategy_returns, ordinary.strategy_returns)
    assert sliced.metrics == ordinary.metrics
    assert sliced.diagnostics["ledger"]["initial_nav"] == 100_000


def test_verified_benchmark_uses_same_scoring_sessions_and_currency():
    frame = _market()
    benchmark = pd.Series(0.01, index=pd.DatetimeIndex(sorted(frame["trade_date"].unique())))
    benchmark.attrs.update(return_type="total_return", currency="CNY", dividend_reinvestment="synthetic_same_session_close")
    result = run_strategy_backtest(_request(), market_df=frame, benchmark_returns=benchmark)
    assert result.diagnostics["benchmark_status"] == "aligned_total_return"
    assert result.benchmark_returns.index.equals(result.strategy_returns.index)
    assert result.metrics["benchmark_total_return"] == pytest.approx(1.01 ** len(result.strategy_returns) - 1)
    benchmark.attrs["currency"] = "USD"
    wrong_currency = run_strategy_backtest(_request(), market_df=frame, benchmark_returns=benchmark)
    assert wrong_currency.diagnostics["benchmark_status"] == "blocked_currency_mismatch"
    assert "benchmark_total_return" not in wrong_currency.metrics


def test_factor_ic_scores_only_registered_window_after_building_complete_labels():
    frame = _market(days=14, stocks=12)
    sessions = pd.DatetimeIndex(sorted(frame["trade_date"].unique()))
    stock = frame["stock_code"].str[1:].astype(float) + 1
    day = frame["trade_date"].rank(method="dense") - 1
    frame["open"] = frame["close"] = 100 * (1 + stock * 0.001) ** day
    start, end = sessions[6], sessions[-2]
    factor = stock.where(frame["trade_date"] >= start, -stock)
    options = dict(n_groups=2, holding_period=2, neutralize_industry=False, neutralize_cap=False,
                   direction_mode="fixed", fixed_direction=1, cost_rate=0,
                   evaluation_start=start.strftime("%Y-%m-%d"), evaluation_end=end.strftime("%Y-%m-%d"))
    with api_context():
        result = run_factor_backtest(frame, precomputed_factor=factor, **options)
        # Warmup factors and out-of-window future closes cannot enter scored IC.
        changed = frame.copy()
        changed.loc[changed["trade_date"] > end, "close"] *= 1000
        comparison = run_factor_backtest(changed, precomputed_factor=stock, **options)
    for key in ["_raw_ic_series", "_raw_rank_ic_series", "_direction_adjusted_ic_series", "_direction_adjusted_rank_ic_series"]:
        series = result[key]
        assert len(series) == 5
        assert series.index.min() == start
        assert series.index.max() == sessions[-4]
        pd.testing.assert_series_equal(series, comparison[key])
    assert result["ic_mean"] == pytest.approx(1)
    assert result["ic_mean"] == pytest.approx(result["_raw_rank_ic_series"].mean())
    assert result["raw_ic_mean"] == pytest.approx(result["_raw_ic_series"].mean())
