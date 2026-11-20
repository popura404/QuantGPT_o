"""Long-only daily research ledger using raw prices and next-session open fills."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, cast

import numpy as np
import pandas as pd

from .contracts import SEMANTICS_VERSION, SimulationConfigV1
from .market_inputs import price_basis


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


@dataclass(frozen=True)
class PreparedMarketContext:
    """Independent, read-only market inputs reusable by multiple portfolio books."""

    shared_sessions: tuple[pd.Timestamp, ...]
    sessions: tuple[pd.Timestamp, ...]
    prices_by_session: Mapping[pd.Timestamp, Mapping[str, Mapping[str, Any]]]
    actions_by_session: Mapping[pd.Timestamp, tuple[Mapping[str, Any], ...]]
    price_basis_status: str


def _freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_value(item) for key, item in value.items()})
    if isinstance(value, (list, tuple, np.ndarray)):
        return tuple(_freeze_value(item) for item in value)
    return deepcopy(value)


def market_actions(frame: pd.DataFrame) -> Sequence[Mapping[str, Any]]:
    """An Alpaca daily bar cannot establish that no corporate action occurred."""
    attrs = frame.attrs
    events = attrs.get("corporate_actions")
    if events is None:
        snapshot = attrs.get("data_snapshot") or {}
        provenance = attrs.get("data_provenance") or []
        if isinstance(provenance, dict):
            provenance = [provenance]
        if snapshot.get("vendor") in {"alpaca_sip", "alpaca_sip_sec"} or any(
            source.get("provider") == "alpaca_sip" for source in provenance if isinstance(source, dict)
        ):
            raise LedgerError("ACTION_LEDGER_REQUIRED", "Freeze the Alpaca panel with a covered corporate-action ledger.")
        return []
    if not isinstance(events, (list, tuple)) or any(not isinstance(event, Mapping) for event in events):
        raise LedgerError("INVALID_CORPORATE_ACTION", "Expected a sequence of event records")
    return events


def event_session(event: Mapping[str, Any]) -> pd.Timestamp:
    try:
        day = pd.Timestamp(event["ex_session"] if "ex_session" in event else event["session"])
        if pd.isna(day) or ("session" in event and pd.Timestamp(event["session"]) != day):
            raise ValueError("missing or conflicting ex-session")
        return cast(pd.Timestamp, day)
    except (KeyError, TypeError, ValueError) as exc:
        raise LedgerError("INVALID_CORPORATE_ACTION", "Expected one valid ex-session") from exc


def cash_entitlement(event: Mapping[str, Any], session: pd.Timestamp) -> tuple[float, pd.Timestamp]:
    delisting = event["type"] == "delisting"
    try:
        value = float(event["terminal_value" if delisting else "per_share"])
        payment = event.get("pay_session")
        if payment is None and not delisting:
            raise ValueError("dividend requires a payment session")
        day = pd.Timestamp(payment) if payment is not None else session
        if not np.isfinite(value) or value < 0 or pd.isna(day) or day < session:
            raise ValueError("invalid value or payment date")
        return value, cast(pd.Timestamp, day)
    except (KeyError, TypeError, ValueError) as exc:
        raise LedgerError("INVALID_CORPORATE_ACTION", "Expected a nonnegative per-share value and payment on/after ex-session") from exc


def prepare_market_context(market_frame: pd.DataFrame) -> PreparedMarketContext:
    """Validate once and detach only the columns/actions consumed by the ledger.

    Building column arrays avoids carrying snapshot/provenance attrs into every
    pandas group and copy. Neither the original frame nor its attrs are edited.
    """
    attrs = market_frame.attrs
    adjustment = price_basis(market_frame)
    if adjustment not in {"unknown", "raw"}:
        raise LedgerError("INCOMPATIBLE_PRICE_BASIS", f"Raw-price ledger cannot consume {adjustment!r} prices")
    stock_column = "security_id" if "security_id" in market_frame else "stock_code"
    date_column = "session" if "session" in market_frame else "trade_date"
    if {stock_column, date_column, "open", "close"} - set(market_frame.columns):
        raise LedgerError("MISSING_PRICE_CONTRACT", "Security/session keys and raw open/close columns are required")
    # A constructor view starts with empty attrs, unlike DataFrame.copy/loc.
    # It is read only here; all retained arrays are detached before use.
    columns = pd.DataFrame(market_frame, copy=False)
    stocks = columns[stock_column].to_numpy(copy=True)
    dates = pd.DatetimeIndex(pd.to_datetime(columns[date_column].to_numpy(copy=True)))
    if dates.hasnans or pd.isna(stocks).any():
        raise LedgerError("INVALID_MARKET_ROW", "Security/session keys must not be missing")
    if pd.MultiIndex.from_arrays([stocks, dates]).has_duplicates:
        raise LedgerError("DUPLICATE_MARKET_ROW", "Expected unique security/session keys")
    shared = pd.DatetimeIndex(pd.to_datetime(attrs.get("calendar_sessions", dates.unique()))).unique().sort_values()
    if shared.hasnans or not dates.isin(shared).all():
        raise LedgerError("CALENDAR_CONTEXT_REQUIRED", "Input sessions must belong to a non-missing shared calendar")
    sessions = shared[(shared >= dates.min()) & (shared <= dates.max())]
    if sessions.empty:
        raise LedgerError("EMPTY_SESSION_CALENDAR", "No sessions available for simulation")
    opening = columns["open"].to_numpy(copy=True)
    closing = columns["close"].to_numpy(copy=True)
    suspended = columns["suspended"].to_numpy(copy=True) if "suspended" in columns else np.zeros(len(dates), dtype=bool)
    prices: dict[pd.Timestamp, dict[str, Mapping[str, Any]]] = {}
    for day, stock, open_price, close_price, halted in zip(dates, stocks, opening, closing, suspended, strict=True):
        prices.setdefault(day, {})[stock] = MappingProxyType({"open": open_price, "close": close_price, "suspended": halted})
    actions: dict[pd.Timestamp, list[Mapping[str, Any]]] = {}
    for event in market_actions(market_frame):
        day = event_session(event)
        fields = {key: event[key] for key in ("event_id", "security_id", "type", "ratio", "per_share", "terminal_value", "pay_session") if key in event}
        actions.setdefault(day, []).append(_freeze_value(fields))
    return PreparedMarketContext(
        shared_sessions=tuple(shared), sessions=tuple(sessions),
        prices_by_session=MappingProxyType({day: MappingProxyType(rows) for day, rows in prices.items()}),
        actions_by_session=MappingProxyType({day: tuple(events) for day, events in actions.items()}),
        price_basis_status="declared" if adjustment in {"raw", "none", "unadjusted"} else "unverified_supplied_frame",
    )


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
        self.delisted: set[str] = set()

    @staticmethod
    def _price(prices: Mapping[str, Mapping[str, Any]], security_id: str, name: str) -> float:
        value = prices.get(security_id, {}).get(name)
        if value is None or not np.isfinite(value) or float(value) <= 0:
            raise LedgerError("MISSING_VALUATION_PRICE" if name == "close" else "MISSING_FILL_PRICE", f"{security_id}: invalid raw {name}")
        return float(value)

    def _actions(self, session: pd.Timestamp, actions: Sequence[Mapping[str, Any]]) -> None:
        for event_id, item in list(self.receivables.items()):
            if pd.Timestamp(item["pay_session"]) <= session:
                self.cash += item["amount"]
                del self.receivables[event_id]
        # Preserve the supplied split/dividend order. Terminal value is per
        # share remaining after all same-session entitlements.
        for event in sorted(actions, key=lambda item: item["type"] == "delisting"):
            security_id = str(event["security_id"])
            kind = event["type"]
            quantity = self.positions.get(security_id, 0.0)
            if quantity == 0 and kind != "delisting":
                continue  # Opening purchases do not obtain earlier entitlements.
            event_id = str(event["event_id"])
            if event_id in self.seen_events or security_id in self.delisted:
                raise LedgerError("DUPLICATE_CORPORATE_ACTION", event_id)
            if kind == "split":
                ratio = float(event["ratio"])
                if not np.isfinite(ratio) or ratio <= 0:
                    raise LedgerError("INVALID_CORPORATE_ACTION", "Split ratio must be positive and finite")
                self.positions[security_id] *= ratio
                self.previous_prices[security_id] /= ratio
            elif kind in {"cash_dividend", "delisting"}:
                per_share, pay_session = cash_entitlement(event, session)
                amount = quantity * per_share
                if kind == "delisting":
                    self.delisted.add(security_id)
                    self.positions.pop(security_id, None)
                    self.previous_prices.pop(security_id, None)
                if pay_session <= session:
                    self.cash += amount
                elif amount > 0:
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

    def step(self, session: Any, prices: Mapping[str, Mapping[str, Any]], *, targets: dict[str, float] | None = None,
             fills: list[dict] | None = None, actions: Sequence[Mapping[str, Any]] | None = None,
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
            # A previously generated target cannot buy a terminated security,
            # even when stale prices are present. Its allocation stays in cash.
            targets = {asset: weight for asset, weight in targets.items() if asset not in self.delisted}
            if not self.positions and self.cash == 0:
                targets = {}
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
                available = self.cash + sum(current.values())
                opening_nav = available + sum(item["amount"] for item in self.receivables.values())
                invested_fraction = sum(targets.values())
                # Preserve relative target sizes inside the settled budget even
                # when dividends have entered NAV but cannot yet fund orders.
                target_nav = min(opening_nav, available / invested_fraction) if invested_fraction else opening_nav
                fee_rate = (self.config.fees_bps + self.config.slippage_bps) / 10000.0
                requested_notional = sum(abs(targets.get(s, 0) * target_nav - current[s]) for s in assets)
                requested_turnover = requested_notional / opening_nav / 2 if opening_nav else 0.0
                if max_turnover is not None and requested_turnover > max_turnover + 1e-12:
                    skipped = True
                else:
                    low, high = 0.0, target_nav
                    for _ in range(64):
                        post_fee_nav = (low + high) / 2
                        cost = fee_rate * sum(abs(targets.get(s, 0) * post_fee_nav - current[s]) for s in assets)
                        if post_fee_nav + cost > opening_nav or invested_fraction * post_fee_nav + cost > available:
                            high = post_fee_nav
                        else:
                            low = post_fee_nav
                    post_fee_nav = low
                    desired = {s: targets.get(s, 0) * post_fee_nav / opening[s] for s in assets}
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
                    if security_id in self.delisted:
                        raise LedgerError("ASSET_NOT_TRADABLE", f"{security_id} has delisted")
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
            "fees": fee, "cost": fee / prior_nav if prior_nav else 0.0,
            "turnover": traded_notional / prior_nav / 2 if prior_nav else 0.0,
            "net_return": self.nav / prior_nav - 1 if prior_nav else 0.0,
            "gross_return": (self.nav + fee) / prior_nav - 1 if prior_nav else 0.0,
            "skipped": skipped,
        }


def simulate_target_weights(market_frame: pd.DataFrame | PreparedMarketContext, target_weights: pd.DataFrame, config: SimulationConfigV1,
                            *, evaluation_start: Any = None, evaluation_end: Any = None,
                            max_turnover: float | None = None) -> LedgerResult:
    """Signals on T fill only at the next observed *shared* session's raw open."""
    context = market_frame if isinstance(market_frame, PreparedMarketContext) else prepare_market_context(market_frame)
    shared_sessions = pd.DatetimeIndex(context.shared_sessions)
    sessions = pd.DatetimeIndex(context.sessions)
    start = pd.Timestamp(evaluation_start) if evaluation_start is not None else pd.Timestamp(cast(Any, sessions[0]))
    end = pd.Timestamp(evaluation_end) if evaluation_end is not None else pd.Timestamp(cast(Any, sessions[-1]))
    if evaluation_end is None and any(day >= end and day in shared_sessions and any(e["type"] == "delisting" for e in events)
                                     for day, events in context.actions_by_session.items()):
        # The last constituent can disappear before its terminal entitlement
        # pays. The published calendar still carries the cash-only portfolio.
        end = pd.Timestamp(cast(Any, shared_sessions[-1]))
    sessions = shared_sessions[(shared_sessions >= sessions[0]) & (shared_sessions <= end)]
    if config.starting_state_session is not None and pd.Timestamp(config.starting_state_session) >= start:
        raise LedgerError("INVALID_STARTING_STATE", "Carry-forward source must precede evaluation start")
    evaluation_sessions = sessions[(sessions >= start) & (sessions <= end)]
    if config.evaluation_start_state == "carry_forward":
        if evaluation_sessions.empty:
            raise LedgerError("EMPTY_EVALUATION_WINDOW", "No sessions inside the scoring window")
        prior_sessions = shared_sessions[shared_sessions < evaluation_sessions[0]]
        if config.starting_state_session is None or prior_sessions.empty or pd.Timestamp(config.starting_state_session) != prior_sessions[-1]:
            raise LedgerError("INVALID_STARTING_STATE", "Carry-forward source must be the immediately previous shared session; replay older state first")
    # Target weights may inherit the full market snapshot attrs. These are not
    # signal inputs and must not be copied into every daily target group.
    signal_columns = pd.DataFrame(target_weights, copy=False)
    signals = pd.DataFrame({name: signal_columns[name].to_numpy(copy=True)
                            for name in ("trade_date", "stock_code", "target_weight")}) if not target_weights.empty else pd.DataFrame()
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
    book = ResearchLedger(config)
    book.delisted = {str(event["security_id"]) for day, events in context.actions_by_session.items() if day < start
                     for event in events if event["type"] == "delisting"}
    if book.delisted.intersection(book.positions):
        raise LedgerError("INVALID_STARTING_STATE", "Opening positions contain a previously delisted security")
    rows = []
    for session in sessions[(sessions >= start) & (sessions <= end)]:
        row = book.step(session, context.prices_by_session.get(session, {}), targets=fill_targets.get(session),
                        actions=context.actions_by_session.get(session, ()), max_turnover=max_turnover)
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
        "price_basis_status": context.price_basis_status,
    })
