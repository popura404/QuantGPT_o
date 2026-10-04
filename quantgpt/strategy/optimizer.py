"""Legacy score normalization and clipping; not a covariance risk optimizer."""

from __future__ import annotations

from typing import cast

import pandas as pd

from .risk import apply_risk_rules
from .spec import StrategySpecV0, StrategySpecV1


def optimize_candidate_weights(signals: list[dict], spec: StrategySpecV0 | StrategySpecV1) -> dict:
    if not signals:
        return {"target_weights": [], "cash_weights": [], "risk_logs": [], "method": "legacy_score_normalization"}

    frame = pd.DataFrame(signals).copy()
    required = {"trade_date", "stock_code", "score"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"signals missing columns: {sorted(missing)}")

    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    frame["score"] = frame["score"].clip(lower=0).astype(float)
    raw_rows = []
    for trade_date, group in frame.groupby("trade_date", sort=True):
        total = float(cast(pd.Series, group["score"]).sum())
        if total <= 0:
            group = group.assign(target_weight=1.0 / len(group))
        else:
            group = group.assign(target_weight=group["score"] / total)
        for stock_code, target_weight in group[["stock_code", "target_weight"]].itertuples(index=False, name=None):
            raw_rows.append({
                "trade_date": trade_date,
                "stock_code": stock_code,
                "target_weight": float(target_weight),
            })

    risk_result = apply_risk_rules(pd.DataFrame(raw_rows), spec)
    return {
        "method": "legacy_score_normalization",
        "risk_optimization_verified": False,
        "target_weights": _records(risk_result.target_weights),
        "cash_weights": _records(risk_result.cash_weights),
        "turnover_by_rebalance": _records(risk_result.turnover_by_rebalance),
        "risk_logs": risk_result.risk_logs,
        "notice": "Optimized candidate weights are for research review only, not trading instructions.",
    }


def _records(frame: pd.DataFrame) -> list[dict]:
    if frame.empty:
        return []
    records = []
    for row in frame.to_dict(orient="records"):
        records.append({
            key: pd.Timestamp(value).strftime("%Y-%m-%d") if isinstance(value, pd.Timestamp) else value
            for key, value in row.items()
        })
    return records
