"""Shared market preparation must not change independent portfolio accounting."""

from dataclasses import FrozenInstanceError

import pandas as pd
import pytest

import quantgpt.backtest as factor_backtest
import quantgpt.research.ledger as ledger_module
from quantgpt.research.contracts import SimulationConfigV1
from quantgpt.research.ledger import LedgerError, ResearchLedger, prepare_market_context, simulate_target_weights


def _market():
    sessions = pd.bdate_range("2024-01-02", periods=5)
    frame = pd.DataFrame([
        {"trade_date": day, "stock_code": stock, "open": price, "close": price,
         "volume": float(index + 1), "extra_unused_field": "preserve me"}
        for day, price in zip(sessions, [100., 100., 50., 48., 48.], strict=True)
        for index, stock in enumerate(["A", "B", "C", "D"])
    ])
    frame.attrs.update(adjustment="raw", calendar_sessions=list(sessions), corporate_actions=[
        {"event_id": f"split-{stock}", "security_id": stock, "session": sessions[2], "type": "split", "ratio": 2}
        for stock in ["A", "B", "C", "D"]
    ] + [
        {"event_id": f"dividend-{stock}", "security_id": stock, "session": sessions[3], "type": "cash_dividend",
         "per_share": 2, "pay_session": sessions[4]}
        for stock in ["A", "B", "C", "D"]
    ])
    return frame


def _targets():
    return pd.DataFrame([{"trade_date": "2024-01-02", "stock_code": stock, "target_weight": .5} for stock in ["A", "B"]])


def test_prepared_context_and_frame_match_the_step_by_step_accounting_oracle():
    frame = _market()
    config = SimulationConfigV1(rebalance_anchor_session="2024-01-02", initial_cash=1000, fees_bps=10)
    context = prepare_market_context(frame)
    plain = simulate_target_weights(frame, _targets(), config)
    prepared = simulate_target_weights(context, _targets(), config)
    book = ResearchLedger(config)
    expected = []
    for index, (day, group) in enumerate(frame.groupby("trade_date")):
        # Independent preparation follows the original ledger's per-day input
        # path, while the fixture has hand-calculated split/dividend invariants.
        prices = group.set_index("stock_code").to_dict("index")
        events = [event for event in frame.attrs["corporate_actions"] if event["session"] == day]
        expected.append(book.step(day, prices, targets={"A": .5, "B": .5} if index == 1 else None, actions=events))
    pd.testing.assert_frame_equal(prepared.states, pd.DataFrame(expected))
    pd.testing.assert_frame_equal(prepared.trades, pd.DataFrame(book.trades))
    for name in ("returns", "gross_returns"):
        pd.testing.assert_series_equal(getattr(plain, name), getattr(prepared, name))
    for name in ("states", "trades", "costs", "turnover"):
        pd.testing.assert_frame_equal(getattr(plain, name), getattr(prepared, name))
    assert prepared.states.iloc[-1]["nav"] == pytest.approx(1000 / 1.001)
    assert prepared.states.iloc[-1]["receivables"] == {}


def test_context_is_detached_and_immutable_at_every_consumed_level():
    frame = _market()
    context = prepare_market_context(frame)
    day = context.sessions[2]
    with pytest.raises(FrozenInstanceError):
        context.price_basis_status = "changed"
    with pytest.raises(TypeError):
        context.prices_by_session[day]["A"]["open"] = 999
    with pytest.raises(TypeError):
        context.actions_by_session[day][0]["ratio"] = 999
    with pytest.raises(TypeError):
        context.prices_by_session[day]["NEW"] = {}
    frame.loc[:, "close"] = 999
    frame.attrs["corporate_actions"][0]["ratio"] = 999
    frame.attrs["calendar_sessions"].clear()
    assert context.prices_by_session[day]["A"]["close"] == 50
    assert context.actions_by_session[day][0]["ratio"] == 2
    assert len(context.shared_sessions) == 5


def test_repeated_books_do_not_mutate_input_or_each_other():
    frame = _market()
    before = frame.copy(deep=True)
    targets = _targets()
    targets_before = targets.copy(deep=True)
    context = prepare_market_context(frame)
    config = SimulationConfigV1(rebalance_anchor_session="2024-01-02", fees_bps=10)
    first = simulate_target_weights(context, targets, config)
    second = simulate_target_weights(context, targets, config)
    pd.testing.assert_frame_equal(frame, before)
    assert frame.attrs == before.attrs
    pd.testing.assert_frame_equal(targets, targets_before)
    pd.testing.assert_frame_equal(first.states, second.states)
    first.states.iloc[-1]["positions"]["A"] = 999
    assert second.states.iloc[-1]["positions"]["A"] != 999


def test_unconsumed_attrs_are_never_deep_copied_by_preparation_or_simulation():
    class UncopyableMetadata:
        def __deepcopy__(self, memo):
            pytest.fail("unconsumed market/target metadata must not enter pandas copies")

    frame = _market()
    frame.attrs["unconsumed_metadata"] = UncopyableMetadata()
    targets = _targets()
    targets.attrs["unconsumed_metadata"] = UncopyableMetadata()
    context = prepare_market_context(frame)
    result = simulate_target_weights(context, targets, SimulationConfigV1(rebalance_anchor_session="2024-01-02"))
    assert len(result.states) == 5
    assert set(context.prices_by_session[context.sessions[0]]["A"]) == {"open", "close", "suspended"}


def test_factor_groups_prepare_market_once_and_share_the_context(monkeypatch):
    frame = _market()
    # Avoid a dividend cash constraint on the rebalance date: this test checks
    # construction count, while action accounting is exercised above.
    frame["open"] = frame["close"] = 100.
    frame.attrs["corporate_actions"] = []
    prepared = []
    consumed = []
    original_prepare = ledger_module.prepare_market_context
    original_simulate = factor_backtest.simulate_target_weights

    def tracked_prepare(market):
        context = original_prepare(market)
        prepared.append(context)
        return context

    def tracked_simulate(market, *args, **kwargs):
        consumed.append(market)
        return original_simulate(market, *args, **kwargs)

    monkeypatch.setattr(factor_backtest, "prepare_market_context", tracked_prepare)
    monkeypatch.setattr(ledger_module, "prepare_market_context", tracked_prepare)
    monkeypatch.setattr(factor_backtest, "simulate_target_weights", tracked_simulate)
    result = factor_backtest.run_factor_backtest(frame, expression="volume", n_groups=2, holding_period=1,
        neutralize_industry=False, neutralize_cap=False, direction_mode="fixed", fixed_direction=1, cost_rate=0)
    assert len(result["_group_ledgers"]) == len(consumed) == 2
    assert len(prepared) == 1
    assert all(context is prepared[0] for context in consumed)


def test_context_prefers_stable_identity_and_preserves_complete_calendar():
    frame = _market().rename(columns={"stock_code": "security_id", "trade_date": "session"})
    frame["stock_code"] = "conflicting alias"
    frame["trade_date"] = "1990-01-01"
    missing = frame["session"].unique()[2]
    frame = frame[frame["session"] != missing].copy()
    context = prepare_market_context(frame)
    assert missing in context.sessions
    assert missing not in context.prices_by_session
    with pytest.raises(LedgerError, match="MISSING_VALUATION_PRICE"):
        simulate_target_weights(context, _targets(), SimulationConfigV1(rebalance_anchor_session="2024-01-02"))


@pytest.mark.parametrize("change,error", [
    ("adjusted", "INCOMPATIBLE_PRICE_BASIS"),
    ("duplicate", "DUPLICATE_MARKET_ROW"),
    ("missing_open", "MISSING_PRICE_CONTRACT"),
    ("incomplete_calendar", "CALENDAR_CONTEXT_REQUIRED"),
])
def test_preparation_keeps_market_contract_checks(change, error):
    frame = _market()
    if change == "adjusted":
        frame.attrs["adjustment"] = "qfq"
    elif change == "duplicate":
        frame = pd.concat([frame, frame.iloc[[0]]])
    elif change == "missing_open":
        frame = frame.drop(columns="open")
    else:
        frame.attrs["calendar_sessions"] = frame.attrs["calendar_sessions"][1:]
    with pytest.raises(LedgerError, match=error):
        prepare_market_context(frame)
