"""Risk rule application for StrategySpec v0."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .spec import StrategySpecV0


@dataclass(slots=True)
class RiskApplicationResult:
    target_weights: pd.DataFrame
    cash_weights: pd.DataFrame
    turnover_by_rebalance: pd.DataFrame
    risk_logs: list[dict]


def apply_risk_rules(target_weights: pd.DataFrame, spec: StrategySpecV0) -> RiskApplicationResult:
    """Apply no-short, max-weight, and max-turnover rules in MVP order."""
    columns = ["trade_date", "stock_code", "target_weight"]
    if target_weights.empty:
        return RiskApplicationResult(
            target_weights=pd.DataFrame(columns=columns),
            cash_weights=pd.DataFrame(columns=["trade_date", "cash_weight"]),
            turnover_by_rebalance=pd.DataFrame(columns=["trade_date", "turnover", "requested_turnover", "skipped"]),
            risk_logs=[],
        )

    target_weights = target_weights.copy()
    target_weights["trade_date"] = pd.to_datetime(target_weights["trade_date"])
    if not spec.risk_rules.allow_short and (target_weights["target_weight"] < 0).any():
        raise ValueError("Negative weights are not allowed when allow_short=false")

    max_weight = spec.risk_rules.max_asset_weight
    max_turnover = spec.risk_rules.max_turnover
    frames = []
    cash_rows = []
    turnover_rows = []
    risk_logs: list[dict] = []
    previous: dict[str, float] | None = None
    previous_cash = 1.0

    for trade_date, group in target_weights.groupby("trade_date", sort=True):
        requested = {
            str(row.stock_code): float(row.target_weight)
            for row in group.itertuples(index=False)
        }
        clipped = {}
        clipped_amount = 0.0
        for stock_code, weight in requested.items():
            new_weight = min(weight, max_weight)
            if new_weight < weight:
                clipped_amount += weight - new_weight
                risk_logs.append({
                    "trade_date": _date_str(trade_date),
                    "code": "MAX_ASSET_WEIGHT_CLIPPED",
                    "stock_code": stock_code,
                    "requested_weight": weight,
                    "target_weight": new_weight,
                    "cash_added": weight - new_weight,
                })
            clipped[stock_code] = new_weight

        cash_weight = max(0.0, 1.0 - sum(clipped.values()))
        if clipped_amount > 0:
            risk_logs.append({
                "trade_date": _date_str(trade_date),
                "code": "CASH_WEIGHT_FROM_CLIPPING",
                "cash_weight": cash_weight,
            })

        requested_turnover = 0.0 if previous is None else _turnover(previous, clipped)
        skipped = False
        effective = clipped
        effective_cash = cash_weight
        effective_turnover = requested_turnover
        if previous is not None and max_turnover is not None and requested_turnover > max_turnover:
            skipped = True
            effective = previous
            effective_cash = previous_cash
            effective_turnover = 0.0
            risk_logs.append({
                "trade_date": _date_str(trade_date),
                "code": "TURNOVER_LIMIT_REBALANCE_SKIPPED",
                "requested_turnover": requested_turnover,
                "max_turnover": max_turnover,
            })

        frames.extend(
            {"trade_date": trade_date, "stock_code": stock_code, "target_weight": weight}
            for stock_code, weight in effective.items()
            if weight > 0
        )
        cash_rows.append({"trade_date": trade_date, "cash_weight": effective_cash})
        turnover_rows.append({
            "trade_date": trade_date,
            "turnover": effective_turnover,
            "requested_turnover": requested_turnover,
            "skipped": skipped,
        })
        previous = dict(effective)
        previous_cash = effective_cash

    return RiskApplicationResult(
        target_weights=pd.DataFrame(frames, columns=columns),
        cash_weights=pd.DataFrame(cash_rows),
        turnover_by_rebalance=pd.DataFrame(turnover_rows),
        risk_logs=risk_logs,
    )


def _turnover(previous: dict[str, float], current: dict[str, float]) -> float:
    assets = set(previous) | set(current)
    return sum(abs(float(current.get(asset, 0.0)) - float(previous.get(asset, 0.0))) for asset in assets) / 2


def _date_str(value) -> str:
    ts = pd.Timestamp(value)
    return ts.strftime("%Y-%m-%d")
