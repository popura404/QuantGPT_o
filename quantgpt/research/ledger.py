"""Long-only daily research ledger using raw prices and next-session open fills."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

import numpy as np
import pandas as pd

from .contracts import SEMANTICS_VERSION, SimulationConfigV1


class LedgerError(ValueError):
    def __init__(self, error_code: str, message: str):
        self.error_code = error_code
        self.retryable = False
        self.next_action = "Supply complete raw prices, event coverage and a compatible simulation configuration."
        super().__init__(f"{error_code}: {message}")


@dataclass
class LedgerResult:
    returns: pd.Series
    gross_returns: pd.Series
    costs: pd.DataFrame
    turnover: pd.DataFrame
    states: pd.DataFrame
    trades: pd.DataFrame
    metadata: dict[str, Any] = field(default_factory=dict)


class ResearchLedger:
    """Quantity/cash/receivable book. All accounting amounts are in base currency."""

    def __init__(self, config: SimulationConfigV1):
        self.config = config
        self.cash = float(config.initial_cash)
        self.positions = {p.security_id: float(p.quantity) for p in config.initial_positions}
        self.previous_prices = {p.security_id: float(p.previous_close) for p in config.initial_positions}
        self.receivables = {
            item.event_id: {"security_id": item.security_id, "amount": float(item.amount), "pay_session": pd.Timestamp(item.pay_session)}
            for item in config.initial_receivables
        }
        self.nav = self.cash + sum(self.positions[s] * self.previous_prices[s] for s in self.positions)
        self.nav += sum(item["amount"] for item in self.receivables.values())
        self.initial_nav = self.nav
        self.trades: list[dict] = []
        self.seen_events: set[str] = set(self.receivables)

    @staticmethod
    def _price(prices: dict, security_id: str, name: str) -> float:
        value = prices.get(security_id, {}).get(name)
        if value is None or not np.isfinite(value) or float(value) <= 0:
            raise LedgerError("MISSING_VALUATION_PRICE" if name == "close" else "MISSING_FILL_PRICE", f"{security_id}: invalid raw {name}")
        return float(value)

    def _actions(self, session: pd.Timestamp, actions: list[dict]) -> None:
        for event_id, item in list(self.receivables.items()):
            if pd.Timestamp(item["pay_session"]) <= session:
                self.cash += item["amount"]
                del self.receivables[event_id]
        for event in actions:
            security_id = str(event["security_id"])
            quantity = self.positions.get(security_id, 0.0)
            if quantity == 0:
                continue  # Opening purchases do not obtain earlier entitlements.
            event_id = str(event["event_id"])
            if event_id in self.seen_events:
                raise LedgerError("DUPLICATE_CORPORATE_ACTION", event_id)
            kind = event["type"]
            if kind == "split":
                ratio = float(event["ratio"])
                if not np.isfinite(ratio) or ratio <= 0:
                    raise LedgerError("INVALID_CORPORATE_ACTION", "Split ratio must be positive and finite")
                self.positions[security_id] *= ratio
                self.previous_prices[security_id] /= ratio
            elif kind == "cash_dividend":
                per_share = float(event["per_share"])
                if not np.isfinite(per_share) or per_share < 0:
                    raise LedgerError("INVALID_CORPORATE_ACTION", "Dividend must be finite and nonnegative")
                amount = quantity * per_share
                pay_session = pd.Timestamp(event["pay_session"])
                if pay_session <= session:
                    self.cash += amount
                else:
                    self.receivables[event_id] = {"security_id": security_id, "amount": amount, "pay_session": pay_session}
            else:
                raise LedgerError("UNSUPPORTED_CORPORATE_ACTION", f"{kind}: {security_id}")
            self.seen_events.add(event_id)

    def _fill(self, session: pd.Timestamp, security_id: str, quantity: float, price: float) -> float:
        if abs(quantity) < 1e-12:
            return 0.0
        notional = quantity * price
        fee = abs(notional) * (self.config.fees_bps + self.config.slippage_bps) / 10000.0
        next_quantity = self.positions.get(security_id, 0.0) + quantity
        if next_quantity < -1e-9 or self.cash - notional - fee < -1e-7:
            raise LedgerError("INFEASIBLE_TRADE", "Trade would create negative quantity or cash")
        self.cash = max(0.0, self.cash - notional - fee)
        if next_quantity > 1e-10:
            self.positions[security_id] = next_quantity
        else:
            self.positions.pop(security_id, None)
        self.trades.append({"trade_date": session, "stock_code": security_id, "quantity": quantity, "price": price, "notional": notional, "fee": fee})
        return fee

    def step(self, session: Any, prices: dict[str, dict], *, targets: dict[str, float] | None = None,
             fills: list[dict] | None = None, actions: list[dict] | None = None,
             max_turnover: float | None = None) -> dict:
        """Apply entitlement, fills and close marks once for a single session."""
        session = pd.Timestamp(session)
        prior_nav = self.nav
        self._actions(session, actions or [])
        fee = 0.0
        traded_notional = 0.0
        skipped = False
        if fills is not None and targets is not None:
            raise LedgerError("INVALID_SIMULATION_INPUT", "Use explicit fills or target weights, not both")
        if targets is not None:
            if any(not np.isfinite(weight) or weight < 0 for weight in targets.values()) or sum(targets.values()) > 1 + 1e-10:
                raise LedgerError("INFEASIBLE_TARGET_WEIGHTS", "Weights must be finite, nonnegative and sum to at most one")
            assets = sorted(set(self.positions) | set(targets))
            try:
                opening = {s: self._price(prices, s, "open") for s in assets}
                if any(bool(prices.get(s, {}).get("suspended", False)) for s in assets):
                    raise LedgerError("ASSET_NOT_TRADABLE", "Requested rebalance contains suspended assets")
            except LedgerError:
                if self.config.missing_fill_policy != "skip_and_hold":
                    raise
                skipped = True
                opening = {}
            if not skipped:
                current = {s: self.positions.get(s, 0.0) * opening[s] for s in assets}
                opening_nav = self.cash + sum(current.values()) + sum(item["amount"] for item in self.receivables.values())
                fee_rate = (self.config.fees_bps + self.config.slippage_bps) / 10000.0
                requested_turnover = sum(abs(targets.get(s, 0) * opening_nav - current[s]) for s in assets) / opening_nav / 2
                if max_turnover is not None and requested_turnover > max_turnover + 1e-12:
                    skipped = True
                else:
                    # Solve post-fee NAV so fully invested targets reserve their own fees.
                    low, high = 0.0, opening_nav
                    for _ in range(64):
                        post_fee_nav = (low + high) / 2
                        cost = fee_rate * sum(abs(targets.get(s, 0) * post_fee_nav - current[s]) for s in assets)
                        if post_fee_nav + cost > opening_nav:
                            high = post_fee_nav
                        else:
                            low = post_fee_nav
                    post_fee_nav = low
                    desired = {s: targets.get(s, 0) * post_fee_nav / opening[s] for s in assets}
                    # Receivables are NAV but cannot fund purchases before payment.
                    cash_required = sum(desired[s] * opening[s] for s in assets) + fee_rate * sum(abs(desired[s] * opening[s] - current[s]) for s in assets)
                    available = self.cash + sum(current.values())
                    if cash_required > available + 1e-7:
                        raise LedgerError("INSUFFICIENT_SETTLED_CASH", "Unpaid dividend receivables cannot fund purchases")
                    changes = {s: desired[s] - self.positions.get(s, 0) for s in assets}
                    for security_id in sorted(assets, key=lambda s: changes[s]):
                        quantity = changes[security_id]
                        fee += self._fill(session, security_id, quantity, opening[security_id])
                        traded_notional += abs(quantity * opening[security_id])
        elif fills:
            executable_fills = fills
            try:
                for fill in fills:
                    security_id = str(fill["security_id"])
                    self._price(prices, security_id, "open")
                    if bool(prices.get(security_id, {}).get("suspended", False)):
                        raise LedgerError("ASSET_NOT_TRADABLE", security_id)
            except LedgerError:
                if self.config.missing_fill_policy != "skip_and_hold":
                    raise
                executable_fills = []
                skipped = True
            for fill in sorted(executable_fills, key=lambda item: item["quantity"]):
                security_id = str(fill["security_id"])
                raw_price = self._price(prices, security_id, "open")
                if not np.isclose(raw_price, float(fill["raw_price"])):
                    raise LedgerError("INVALID_FILL_PRICE", "Explicit fill must equal the raw session open")
                quantity = float(fill["quantity"])
                if not np.isfinite(quantity):
                    raise LedgerError("INVALID_FILL_QUANTITY", security_id)
                fee += self._fill(session, security_id, quantity, raw_price)
                traded_notional += abs(quantity * raw_price)
        closes = {s: self._price(prices, s, "close") for s in self.positions}
        self.nav = self.cash + sum(self.positions[s] * closes[s] for s in self.positions)
        self.nav += sum(item["amount"] for item in self.receivables.values())
        self.previous_prices = closes
        return {
            "trade_date": session, "cash": self.cash, "nav": self.nav,
            "positions": dict(self.positions), "receivables": {k: item["amount"] for k, item in self.receivables.items()},
            "fees": fee, "cost": fee / prior_nav, "turnover": traded_notional / prior_nav / 2,
            "net_return": self.nav / prior_nav - 1, "gross_return": (self.nav + fee) / prior_nav - 1,
            "skipped": skipped,
        }


def simulate_target_weights(market_frame: pd.DataFrame, target_weights: pd.DataFrame, config: SimulationConfigV1,
                            *, evaluation_start: Any = None, evaluation_end: Any = None,
                            max_turnover: float | None = None) -> LedgerResult:
    """Signals on T fill only at the next observed *shared* session's raw open."""
    frame = market_frame.copy()
    if "security_id" in frame:
        frame["stock_code"] = frame["security_id"]
    if "session" in frame:
        frame["trade_date"] = frame["session"]
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    snapshot = frame.attrs.get("data_snapshot") or {}
    query = snapshot.get("query_params") or {}
    source = frame.attrs.get("source_metadata") or {}
    adjustment = frame.attrs.get("adjustment") or source.get("adjustment") or query.get("adjustment_type")
    if adjustment not in {None, "unknown", "raw", "none", "unadjusted"}:
        raise LedgerError("INCOMPATIBLE_PRICE_BASIS", f"Raw-price ledger cannot consume {adjustment!r} prices")
    if frame.duplicated(["stock_code", "trade_date"]).any():
        raise LedgerError("DUPLICATE_MARKET_ROW", "Expected unique security/session keys")
    required = {"open", "close"}
    if required - set(frame.columns):
        raise LedgerError("MISSING_PRICE_CONTRACT", "Raw open and close columns are required")
    shared_sessions = pd.DatetimeIndex(sorted(pd.to_datetime(frame.attrs.get("calendar_sessions", frame["trade_date"].unique())).unique()))
    sessions = shared_sessions
    sessions = sessions[(sessions >= frame["trade_date"].min()) & (sessions <= frame["trade_date"].max())]
    if sessions.empty:
        raise LedgerError("EMPTY_SESSION_CALENDAR", "No sessions available for simulation")
    start = pd.Timestamp(evaluation_start) if evaluation_start is not None else pd.Timestamp(cast(Any, sessions[0]))
    end = pd.Timestamp(evaluation_end) if evaluation_end is not None else pd.Timestamp(cast(Any, sessions[-1]))
    if config.starting_state_session is not None and pd.Timestamp(config.starting_state_session) >= start:
        raise LedgerError("INVALID_STARTING_STATE", "Carry-forward source must precede evaluation start")
    evaluation_sessions = sessions[(sessions >= start) & (sessions <= end)]
    if config.evaluation_start_state == "carry_forward":
        if evaluation_sessions.empty:
            raise LedgerError("EMPTY_EVALUATION_WINDOW", "No sessions inside the scoring window")
        prior_sessions = shared_sessions[shared_sessions < evaluation_sessions[0]]
        if config.starting_state_session is None or prior_sessions.empty or pd.Timestamp(config.starting_state_session) != prior_sessions[-1]:
            raise LedgerError("INVALID_STARTING_STATE", "Carry-forward source must be the immediately previous shared session; replay older state first")
    signals = target_weights.copy()
    if not signals.empty:
        signals["trade_date"] = pd.to_datetime(signals["trade_date"])
        if signals.duplicated(["trade_date", "stock_code"]).any():
            raise LedgerError("DUPLICATE_TARGET_WEIGHT", "Expected unique security/session weights")
    by_signal = {
        pd.Timestamp(cast(Any, day)): group.set_index("stock_code")["target_weight"].astype(float).to_dict()
        for day, group in signals.groupby("trade_date")
    } if not signals.empty else {}
    # Only the immediately preceding session may create a fill. Missing signals
    # hold existing quantities; they never forward-fill stale targets into trades.
    fill_targets = {sessions[i + 1]: by_signal[day] for i, day in enumerate(sessions[:-1]) if day in by_signal}
    prices_by_date = {pd.Timestamp(cast(Any, day)): group.set_index("stock_code").to_dict("index") for day, group in frame.groupby("trade_date")}
    events = frame.attrs.get("corporate_actions", [])
    book = ResearchLedger(config)
    rows = []
    for session in sessions[(sessions >= start) & (sessions <= end)]:
        actions = [event for event in events if pd.Timestamp(event["session"]) == session]
        row = book.step(session, prices_by_date.get(session, {}), targets=fill_targets.get(session), actions=actions, max_turnover=max_turnover)
        rows.append(row)
    states = pd.DataFrame(rows)
    if states.empty:
        empty = pd.Series(dtype=float)
        return LedgerResult(empty, empty, pd.DataFrame(), pd.DataFrame(), states, pd.DataFrame())
    index = pd.DatetimeIndex(states["trade_date"])
    returns = pd.Series(states["net_return"].to_numpy(), index=index, name="strategy")
    gross_returns = pd.Series(states["gross_return"].to_numpy(), index=index, name="gross_strategy")
    costs = cast(pd.DataFrame, states[["trade_date", "cost", "fees"]].copy())
    turnover = cast(pd.DataFrame, states[["trade_date", "turnover", "skipped"]].copy())
    return LedgerResult(returns, gross_returns, costs, turnover, states, pd.DataFrame(book.trades), {
        "semantics_version": SEMANTICS_VERSION, "simulation_config": config.model_dump(mode="json"),
        "initial_nav": book.initial_nav, "initial_entry_charged": True,
        "price_basis": "raw", "fill_time": "next_session_open", "external_integration": "not_run",
        "price_basis_status": "declared" if adjustment in {"raw", "none", "unadjusted"} else "unverified_supplied_frame",
    })
