"""Portfolio target construction for StrategySpec v0."""

from __future__ import annotations

import pandas as pd

from .spec import StrategySpecV0


def build_equal_weight_portfolio(signals: pd.DataFrame, spec: StrategySpecV0) -> pd.DataFrame:
    """Convert eligible signals into equal-weight target weights."""
    if spec.portfolio_rule.weighting != "equal_weight":
        raise ValueError("StrategySpec v0 only supports equal_weight")

    frames = []
    for trade_date, group in signals.groupby("trade_date", sort=True):
        selected = group[group["eligibility"]].copy()
        if selected.empty:
            continue
        weight = 1.0 / len(selected)
        selected["target_weight"] = weight
        selected["trade_date"] = trade_date
        frames.append(selected[["trade_date", "stock_code", "target_weight"]])

    if not frames:
        return pd.DataFrame(columns=["trade_date", "stock_code", "target_weight"])
    return pd.concat(frames, ignore_index=True)
