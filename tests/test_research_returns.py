"""Economic invariants shared by factor/strategy labels and annualization."""

import numpy as np
import pandas as pd
import pytest

from quantgpt.backtest import _calc_ic_series, run_factor_backtest
from quantgpt.research.returns import annualized_return, forward_total_returns
from quantgpt.strategy.backtest import StrategyBacktestRequest, _strategy_metrics, run_strategy_backtest
from quantgpt.strategy.spec import example_strategy_spec_v1


def test_terminal_labels_match_entitlements_with_missing_quotes_and_respect_window_end():
    frame = pd.DataFrame({"stock_code": ["A"] * 3, "trade_date": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-08"]),
                          "close": [100, 100, 888]})
    frame.attrs["calendar_sessions"] = pd.bdate_range("2024-01-02", "2024-01-09")
    frame.attrs["corporate_actions"] = [
        {"event_id": "exit", "security_id": "A", "type": "delisting", "ex_session": "2024-01-04", "terminal_value": 45},
        {"event_id": "split", "security_id": "A", "type": "split", "session": "2024-01-04", "ratio": 2},
        {"event_id": "div", "security_id": "A", "type": "cash_dividend", "session": "2024-01-04", "per_share": 1,
         "pay_session": "2024-01-12"},
    ]
    prior = frame.iloc[:2].copy()
    assert forward_total_returns(prior, 1, evaluation_end="2024-01-04").iloc[-1] == pytest.approx(-.08)
    assert pd.isna(forward_total_returns(prior, 2, evaluation_end="2024-01-04").iloc[-1])
    assert forward_total_returns(frame, 3).iloc[1] == pytest.approx(-.08)
    assert pd.isna(forward_total_returns(frame, 1, evaluation_end="2024-01-09").iloc[-1])


def _market():
    sessions = pd.bdate_range("2024-01-02", periods=5)
    frame = pd.DataFrame([
        {"trade_date": day, "stock_code": f"S{stock:02}",
         "open": 100 * (1 + (stock + 1) / 100) ** offset,
         "close": 100 * (1 + (stock + 1) / 100) ** offset, "volume": float(stock + 1)}
        for offset, day in enumerate(sessions) for stock in range(10)
    ])
    frame["high"] = frame["low"] = frame["close"]
    frame["amount"] = frame["volume"] * frame["close"]
    frame["factor_value"] = frame["volume"]
    frame.attrs.update(adjustment="raw", calendar_sessions=list(sessions), corporate_actions=[])
    return frame


def _with_action(frame, kind):
    changed = frame.copy()
    session = frame["trade_date"].max() if kind == "cash_dividend" else sorted(frame["trade_date"].unique())[2]
    stocks = [f"S{i:02}" for i in range(5, 10)]
    selected = (changed["trade_date"] >= session) & changed["stock_code"].isin(stocks)
    if kind == "split":
        changed.loc[selected, ["open", "close", "high", "low"]] /= 2
    else:
        changed.loc[selected, ["open", "close", "high", "low"]] -= 20
    changed.attrs["corporate_actions"] = [
        {"event_id": f"event-{stock}", "security_id": stock, "session": session, "type": kind,
         **({"ratio": 2} if kind == "split" else {"per_share": 20, "pay_session": session})}
        for stock in stocks
    ]
    return changed


@pytest.mark.parametrize("kind", ["split", "cash_dividend"])
def test_formal_factor_entry_preserves_ic_and_wealth_for_equivalent_actions(kind):
    original = _market()
    options = {"expression": "volume", "n_groups": 2, "holding_period": 1, "cost_rate": 0,
               "neutralize_industry": False, "neutralize_cap": False, "direction_mode": "fixed", "fixed_direction": 1}
    base = run_factor_backtest(original, **options)
    changed = run_factor_backtest(_with_action(original, kind), **options)
    pd.testing.assert_series_equal(base["strategy_returns"], changed["strategy_returns"], atol=1e-12, rtol=1e-12)
    pd.testing.assert_series_equal(base["_raw_rank_ic_series"], changed["_raw_rank_ic_series"])
    pd.testing.assert_series_equal(base["_raw_ic_series"], changed["_raw_ic_series"], atol=1e-12, rtol=1e-12)


@pytest.mark.parametrize("kind", ["split", "cash_dividend"])
def test_formal_strategy_entry_uses_same_action_aware_ic(kind):
    original = _market()
    spec = example_strategy_spec_v1()
    spec.update(universe="small_scale", factors=[{"id": "volume", "expression": "volume", "weight": 1,
                                                  "direction": "higher_is_better"}])
    spec["signal_rules"] = {"type": "rank_threshold", "top_n": 5}
    spec["portfolio_rule"] = {"weighting": "equal_weight", "rebalance_period": 1}
    spec["risk_rules"].update(max_asset_weight=1.0, max_turnover=None)
    spec["cost_model"]["bps"] = 0
    spec["validation"].update(run_strategy_anti_overfit=False, run_strategy_rolling_validation=False,
                              data_quality={"enabled": False})
    req = StrategyBacktestRequest.model_validate({"spec": spec, "start_date": "2024-01-02", "end_date": "2024-01-08"})
    base = run_strategy_backtest(req, market_df=original)
    changed = run_strategy_backtest(req, market_df=_with_action(original, kind))
    pd.testing.assert_series_equal(base.strategy_returns, changed.strategy_returns, atol=1e-12, rtol=1e-12)
    assert changed.metrics["ic_mean"] == pytest.approx(base.metrics["ic_mean"])
    assert changed.metrics["annual_return"] == pytest.approx(base.metrics["annual_return"])


def test_explicit_calendar_gap_never_compresses_the_forward_horizon():
    frame = _market()
    missing_day = sorted(frame["trade_date"].unique())[1]
    frame = frame[frame["trade_date"] != missing_day].copy()
    labels = forward_total_returns(frame, 1)
    first_day = frame["trade_date"].min()
    assert labels[frame["trade_date"] == first_day].isna().all()
    assert labels[frame["trade_date"] == sorted(frame["trade_date"].unique())[1]].notna().all()
    _, rank_ic = _calc_ic_series(frame, 1)
    assert first_day not in rank_ic.index


def test_multiple_actions_preserve_order_and_count_unpaid_dividends_in_wealth():
    frame = pd.DataFrame({"trade_date": pd.bdate_range("2024-01-02", periods=4),
                          "stock_code": ["A"] * 4, "close": [100., 50., 48., 48.]})
    frame.attrs["corporate_actions"] = [
        {"event_id": "split", "security_id": "A", "session": "2024-01-03", "type": "split", "ratio": 2},
        {"event_id": "dividend", "security_id": "A", "session": "2024-01-04", "type": "cash_dividend",
         "per_share": 2, "pay_session": "2024-01-10"},
    ]
    assert forward_total_returns(frame, 2).iloc[0] == pytest.approx(0)
    assert forward_total_returns(frame, 1).iloc[1] == pytest.approx(0)
    # Buying at the ex-date close cannot receive that earlier entitlement.
    assert forward_total_returns(frame, 1).iloc[2] == pytest.approx(0)
    # A label ending before the dividend does not include it either.
    assert forward_total_returns(frame, 1).iloc[0] == pytest.approx(0)


@pytest.mark.parametrize("split_first,closing_price", [(True, 47.), (False, 48.5)])
def test_same_session_event_order_matches_quantity_and_entitlement_accounting(split_first, closing_price):
    frame = pd.DataFrame({"trade_date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
                          "stock_code": ["A", "A"], "close": [100., closing_price]})
    split = {"event_id": "split", "security_id": "A", "session": "2024-01-03", "type": "split", "ratio": 2}
    dividend = {"event_id": "cash", "security_id": "A", "session": "2024-01-03", "type": "cash_dividend",
                "per_share": 3, "pay_session": "2024-01-10"}
    frame.attrs["corporate_actions"] = [split, dividend] if split_first else [dividend, split]
    assert forward_total_returns(frame, 1).iloc[0] == pytest.approx(0)


def test_future_events_and_row_order_cannot_change_current_window_labels():
    frame = _market()
    original = forward_total_returns(frame, 1)
    frame.attrs["corporate_actions"] = [
        {"event_id": "future", "security_id": "S00", "session": "2025-01-01", "type": "unsupported_future_event"}
    ]
    pd.testing.assert_series_equal(forward_total_returns(frame, 1), original)
    shuffled = frame.sample(frac=1, random_state=42)
    pd.testing.assert_series_equal(forward_total_returns(shuffled, 1).sort_index(), original)


@pytest.mark.parametrize("event,match", [
    ({"type": "split", "ratio": 0}, "INVALID_CORPORATE_ACTION"),
    ({"type": "split", "ratio": np.nan}, "INVALID_CORPORATE_ACTION"),
    ({"type": "cash_dividend", "per_share": -1, "pay_session": "2024-01-04"}, "INVALID_CORPORATE_ACTION"),
    ({"type": "cash_dividend", "per_share": 1}, "INVALID_CORPORATE_ACTION"),
    ({"type": "cash_dividend", "per_share": 1, "pay_session": "2024-01-01"}, "INVALID_CORPORATE_ACTION"),
    ({"type": "spinoff"}, "UNSUPPORTED_CORPORATE_ACTION"),
])
def test_invalid_or_unsupported_actions_never_become_price_only_labels(event, match):
    frame = _market()
    frame.attrs["corporate_actions"] = [{"event_id": "bad", "security_id": "S00", "session": "2024-01-03", **event}]
    with pytest.raises(ValueError, match=match):
        forward_total_returns(frame, 1)


def test_duplicate_actions_and_adjusted_prices_are_rejected():
    frame = _with_action(_market(), "split")
    frame.attrs["corporate_actions"].append(frame.attrs["corporate_actions"][0].copy())
    with pytest.raises(ValueError, match="DUPLICATE_CORPORATE_ACTION"):
        forward_total_returns(frame, 1)
    frame = _market()
    frame.attrs["adjustment"] = "split_adjusted"
    with pytest.raises(ValueError, match="INCOMPATIBLE_PRICE_BASIS"):
        forward_total_returns(frame, 1)


def test_missing_and_invalid_prices_remain_missing_without_forward_filling():
    frame = _market()
    last_day = frame["trade_date"].max()
    frame.loc[(frame["trade_date"] == last_day) & (frame["stock_code"] == "S00"), "close"] = np.nan
    frame.loc[(frame["trade_date"] == last_day) & (frame["stock_code"] == "S01"), "close"] = -1
    labels = forward_total_returns(frame, 1)
    previous_day = sorted(frame["trade_date"].unique())[-2]
    assert labels[(frame["trade_date"] == previous_day) & frame["stock_code"].isin(["S00", "S01"])].isna().all()
    assert labels[frame["trade_date"] == last_day].isna().all()


def test_equal_initial_and_final_wealth_has_zero_annualized_return():
    returns = pd.Series([.1, -1 / 11])
    assert annualized_return(returns) == pytest.approx(0, abs=1e-12)
    metrics = _strategy_metrics(returns, pd.DataFrame(), pd.Series(dtype=float))
    assert metrics["total_return"] == pytest.approx(0)
    assert metrics["annual_return"] == pytest.approx(0, abs=1e-12)


def test_factor_group_annualization_tracks_its_compounded_book():
    frame = _market()
    frame["open"] = frame["close"] = 100.
    middle_day = sorted(frame["trade_date"].unique())[2]
    frame.loc[(frame["trade_date"] == middle_day) & (frame["stock_code"] == "S09"), ["open", "close"]] = 200.
    result = run_factor_backtest(frame, expression="volume", n_groups=2, holding_period=3, cost_rate=0,
                                 neutralize_industry=False, neutralize_cap=False,
                                 direction_mode="fixed", fixed_direction=1)
    assert (1 + result["strategy_returns"]).prod() == pytest.approx(1.)
    assert result["group_returns"][1]["annual_return"] == pytest.approx(0, abs=1e-12)
    spread = result["ls_returns"]
    assert result["long_short_annual"] == pytest.approx((1 + spread).prod() ** (252 / len(spread)) - 1)


def test_geometric_annualization_matches_compounded_wealth_and_bankruptcy():
    returns = pd.Series([.02, -.01, .005])
    assert annualized_return(returns) == pytest.approx((1 + returns).prod() ** (252 / len(returns)) - 1)
    assert annualized_return(pd.Series([.1, -1.])) == -1
    assert annualized_return(pd.Series(dtype=float)) == 0
    for values in [[np.nan], [np.inf], [-1.1]]:
        with pytest.raises(ValueError, match="INVALID_RETURN_EVIDENCE"):
            annualized_return(pd.Series(values))
