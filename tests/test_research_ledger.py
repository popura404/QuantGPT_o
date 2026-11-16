"""Hand-calculated invariants against the actual quantity/cash ledger."""

import json
from pathlib import Path

import pandas as pd
import pytest

from quantgpt.research.contracts import SimulationConfigV1
from quantgpt.research.ledger import LedgerError, ResearchLedger, simulate_target_weights

GOLDEN = json.loads((Path(__file__).parent / "fixtures/research/simulation_golden.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", GOLDEN["cases"], ids=lambda case: case["id"])
def test_ledger_executes_shared_golden_cases(case):
    initial = case["start_state"]
    config = {
        "evaluation_start_state": initial["evaluation_start_state"], "initial_cash": initial["cash"],
        "initial_positions": [{"security_id": security_id, "quantity": quantity, "previous_close": case["previous_close"][security_id]} for security_id, quantity in initial["positions"].items()],
        "fees_bps": case["fees_bps"], "rebalance_anchor_session": "2024-01-02",
    }
    if initial["evaluation_start_state"] == "carry_forward":
        config.update(starting_state_session=initial["source_session"], starting_state_ref={"artifact_id": initial["source_ref"], "project_id": "synthetic", "content_sha256": "a" * 64, "kind": "starting_state"})
    book = ResearchLedger(SimulationConfigV1.model_validate(config))
    prices = {security_id: {"open": price, "close": case["close"][security_id]} for security_id, price in case["open"].items()}
    events = [{**event, "type": "cash_dividend"} for event in case.get("dividends_ex_before_open", [])]
    events += [{**event, "type": "split", "event_id": f"split-{index}"} for index, event in enumerate(case.get("splits_before_open", []))]
    result = book.step(case["session"], prices, fills=case["fills"], actions=events)
    for name in ("cash", "nav", "fees", "net_return"):
        assert result[name] == pytest.approx(case["expected"][name])
    assert result["positions"] == pytest.approx(case["expected"]["positions"])
    assert result["receivables"] == pytest.approx(case["expected"]["receivables"])


def _frame():
    return pd.DataFrame([
        {"trade_date": day, "stock_code": stock, "open": price, "close": price}
        for day, a_price in zip(pd.bdate_range("2024-01-02", periods=4), [100, 100, 200, 100])
        for stock, price in [("A", a_price), ("B", 100)]
    ])


def test_fixed_quantities_do_not_silently_rebalance_daily():
    frame = _frame()
    targets = pd.DataFrame([{"trade_date": "2024-01-02", "stock_code": stock, "target_weight": 0.5} for stock in ["A", "B"]])
    config = SimulationConfigV1(rebalance_anchor_session="2024-01-02", initial_cash=1000)
    result = simulate_target_weights(frame, targets, config)
    assert result.states["nav"].tolist() == pytest.approx([1000, 1000, 1500, 1000])
    assert result.states.iloc[-1]["positions"] == pytest.approx({"A": 5, "B": 5})
    assert (1 + result.returns).prod() - 1 == pytest.approx(0)


def test_fees_include_first_entry_and_both_sides_without_negative_cash():
    frame = _frame()
    frame["open"] = frame["close"] = 100.0
    targets = pd.DataFrame([
        {"trade_date": "2024-01-02", "stock_code": "A", "target_weight": 1.0},
        {"trade_date": "2024-01-03", "stock_code": "B", "target_weight": 1.0},
    ])
    returns = []
    for fees in [0, 10, 100]:
        result = simulate_target_weights(frame, targets, SimulationConfigV1(rebalance_anchor_session="2024-01-02", fees_bps=fees))
        assert (result.states["cash"] >= 0).all()
        assert len(result.trades) == 3
        assert result.trades["fee"].sum() == pytest.approx(result.trades["notional"].abs().sum() * fees / 10000)
        returns.append((1 + result.returns).prod() - 1)
    assert returns[0] > returns[1] > returns[2]


def test_missing_held_valuation_blocks_instead_of_zero_return():
    frame = _frame()
    frame = frame[~((frame["trade_date"] == pd.Timestamp("2024-01-04")) & (frame["stock_code"] == "A"))]
    targets = pd.DataFrame([{"trade_date": "2024-01-02", "stock_code": "A", "target_weight": 1.0}])
    with pytest.raises(LedgerError, match="MISSING_VALUATION_PRICE"):
        simulate_target_weights(frame, targets, SimulationConfigV1(rebalance_anchor_session="2024-01-02"))


def test_no_signal_keeps_fresh_cash_and_never_creates_holdings():
    result = simulate_target_weights(_frame(), pd.DataFrame(), SimulationConfigV1(rebalance_anchor_session="2024-01-02"))
    assert result.returns.eq(0).all()
    assert result.trades.empty
    assert all(not positions for positions in result.states["positions"])


def test_dividend_payment_transfers_receivable_to_cash_without_new_profit():
    config = SimulationConfigV1.model_validate({
        "rebalance_anchor_session": "2024-01-02", "evaluation_start_state": "carry_forward", "initial_cash": 500,
        "initial_positions": [{"security_id": "A", "quantity": 5, "previous_close": 100}],
        "starting_state_session": "2024-01-02", "starting_state_ref": {"artifact_id": "state", "project_id": "p", "kind": "starting_state", "content_sha256": "a" * 64},
    })
    book = ResearchLedger(config)
    prices = {"A": {"open": 98, "close": 98}}
    book.step("2024-01-03", prices, actions=[{"event_id": "dividend", "security_id": "A", "type": "cash_dividend", "per_share": 2, "pay_session": "2024-01-05"}])
    result = book.step("2024-01-05", prices)
    assert result["cash"] == 510
    assert result["receivables"] == {}
    assert result["nav"] == 1000
    assert result["net_return"] == 0


def test_unknown_corporate_action_on_position_blocks():
    book = ResearchLedger(SimulationConfigV1(rebalance_anchor_session="2024-01-02", initial_cash=1000))
    book.step("2024-01-03", {"A": {"open": 100, "close": 100}}, targets={"A": 1})
    with pytest.raises(LedgerError, match="UNSUPPORTED_CORPORATE_ACTION"):
        book.step("2024-01-04", {"A": {"open": 100, "close": 100}}, actions=[{"type": "spinoff", "security_id": "A", "event_id": "spin"}])


def test_carry_forward_source_must_be_immediately_previous_session():
    config = SimulationConfigV1.model_validate({
        "rebalance_anchor_session": "2024-01-02", "evaluation_start_state": "carry_forward", "initial_cash": 500,
        "initial_positions": [{"security_id": "A", "quantity": 5, "previous_close": 100}],
        "starting_state_session": "2024-01-02", "starting_state_ref": {"artifact_id": "state", "project_id": "p", "kind": "starting_state", "content_sha256": "a" * 64},
    })
    with pytest.raises(LedgerError, match="immediately previous"):
        simulate_target_weights(_frame(), pd.DataFrame(), config, evaluation_start="2024-01-04")
    result = simulate_target_weights(_frame(), pd.DataFrame(), config, evaluation_start="2024-01-03")
    assert result.states.iloc[0]["positions"] == {"A": 5}
    assert result.costs["fees"].sum() == 0


@pytest.mark.parametrize("policy", ["block", "skip_and_hold"])
def test_explicit_fills_obey_same_suspension_policy_as_targets(policy):
    book = ResearchLedger(SimulationConfigV1(rebalance_anchor_session="2024-01-02", missing_fill_policy=policy))
    prices = {"A": {"open": 100, "close": 100, "suspended": True}}
    fills = [{"security_id": "A", "quantity": 1, "raw_price": 100}]
    if policy == "block":
        with pytest.raises(LedgerError, match="ASSET_NOT_TRADABLE"):
            book.step("2024-01-03", prices, fills=fills)
    else:
        result = book.step("2024-01-03", prices, fills=fills)
        assert result["positions"] == {}
        assert result["skipped"] is True


def test_adjusted_prices_cannot_be_mislabeled_raw_by_ledger():
    frame = _frame()
    frame.attrs["data_snapshot"] = {"query_params": {"adjustment_type": "qfq"}}
    with pytest.raises(LedgerError, match="INCOMPATIBLE_PRICE_BASIS"):
        simulate_target_weights(frame, pd.DataFrame(), SimulationConfigV1(rebalance_anchor_session="2024-01-02"))


@pytest.mark.parametrize("value,pay_date", [(0, None), (30, None), (30, "2024-01-08")])
def test_frozen_delisting_retires_shares_without_a_quote_and_settles_on_pay_date(tmp_path, value, pay_date):
    from quantgpt.data_snapshots import freeze_market_frame, load_frozen_market_frame

    frame = _frame()
    frame = frame[(frame["stock_code"] == "A") & (frame["trade_date"] <= "2024-01-03")].copy()
    days = pd.bdate_range("2024-01-02", "2024-01-08")
    frame.attrs["calendar_sessions"] = [day.date().isoformat() for day in days]
    event = {"event_id": "delist", "security_id": "A", "type": "delisting", "ex_session": "2024-01-04", "terminal_value": value}
    if pay_date:
        event["pay_session"] = pay_date
    frame.attrs["corporate_actions"] = [event]
    manifest = freeze_market_frame(frame, tmp_path, vendor="fixture")
    restored = load_frozen_market_frame(manifest["snapshot_id"], tmp_path)
    assert restored.attrs["corporate_actions"] == [event]
    targets = pd.DataFrame({"trade_date": days, "stock_code": "A", "target_weight": 1.0})
    result = simulate_target_weights(restored, targets, SimulationConfigV1(rebalance_anchor_session="2024-01-02", initial_cash=100))
    assert result.returns.tolist() == pytest.approx([0, 0, value / 100 - 1, 0, 0])
    assert result.states["nav"].tolist() == pytest.approx([100, 100, value, value, value])
    assert result.states.iloc[2]["receivables"] == ({"delist": value} if pay_date else {})
    assert result.states.iloc[2]["cash"] == (0 if pay_date else value)
    assert result.states.iloc[-1]["cash"] == value
    assert result.states.iloc[-1]["receivables"] == {} and result.states.iloc[2]["positions"] == {}
    assert len(result.trades) == 1 and result.turnover["turnover"].iloc[2:].eq(0).all()


def test_same_session_dividend_and_split_precede_delisting_and_stale_fills_cannot_buy_again():
    book = ResearchLedger(SimulationConfigV1(rebalance_anchor_session="2024-01-02", initial_cash=100))
    stale = {"A": {"open": 100, "close": 100}}
    book.step("2024-01-02", stale, targets={"A": 1})
    result = book.step("2024-01-03", {}, targets={"A": 1}, actions=[
        {"event_id": "terminal", "security_id": "A", "type": "delisting", "terminal_value": 45},
        {"event_id": "split", "security_id": "A", "type": "split", "ratio": 2},
        {"event_id": "div", "security_id": "A", "type": "cash_dividend", "per_share": 1, "pay_session": "2024-01-04"},
    ])
    assert result["cash"] == 90 and result["nav"] == 92 and result["receivables"] == {"div": 2}
    assert book.step("2024-01-04", stale, targets={"A": 1})["positions"] == {}
    with pytest.raises(LedgerError, match="ASSET_NOT_TRADABLE"):
        book.step("2024-01-05", stale, fills=[{"security_id": "A", "quantity": .2, "raw_price": 100}])


@pytest.mark.parametrize("override", [{"terminal_value": -1}, {"terminal_value": float("nan")}, {"pay_session": "2023-12-01"}])
def test_invalid_terminal_value_or_payment_fails_even_before_an_opening_purchase(override):
    book = ResearchLedger(SimulationConfigV1(rebalance_anchor_session="2024-01-02"))
    with pytest.raises(LedgerError, match="INVALID_CORPORATE_ACTION"):
        book.step("2024-01-03", {}, actions=[{"event_id": "delist", "security_id": "A", "type": "delisting",
                                             "terminal_value": 0, **override}])
