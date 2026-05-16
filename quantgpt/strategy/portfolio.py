"""Portfolio target construction for StrategySpec strategies."""

from __future__ import annotations

import pandas as pd

from .spec import StrategySpecV0, StrategySpecV1


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


def build_strategy_portfolio(signals: pd.DataFrame, spec: StrategySpecV0 | StrategySpecV1) -> pd.DataFrame:
    """Convert eligible signals into target weights for the spec weighting rule."""
    weighting = spec.portfolio_rule.weighting
    if weighting == "equal_weight":
        return _build_equal_weight(signals)
    if weighting == "score_weighted":
        return _build_score_weighted(signals)
    raise ValueError(f"Unsupported portfolio weighting: {weighting}")


def _build_equal_weight(signals: pd.DataFrame) -> pd.DataFrame:
    frames = []
    for trade_date, group in signals.groupby("trade_date", sort=True):
        selected = group[group["eligibility"]].copy()
        if selected.empty:
            continue
        selected["target_weight"] = 1.0 / len(selected)
        selected["trade_date"] = trade_date
        frames.append(selected[["trade_date", "stock_code", "target_weight"]])
    if not frames:
        return pd.DataFrame(columns=["trade_date", "stock_code", "target_weight"])
    return pd.concat(frames, ignore_index=True)


def _build_score_weighted(signals: pd.DataFrame) -> pd.DataFrame:
    frames = []
    for trade_date, group in signals.groupby("trade_date", sort=True):
        selected = group[group["eligibility"]].copy()
        if selected.empty:
            continue
        scores = selected["score"].clip(lower=0).astype(float)
        total = float(scores.sum())
        if total <= 0:
            selected["target_weight"] = 1.0 / len(selected)
        else:
            selected["target_weight"] = scores / total
        selected["trade_date"] = trade_date
        frames.append(selected[["trade_date", "stock_code", "target_weight"]])
    if not frames:
        return pd.DataFrame(columns=["trade_date", "stock_code", "target_weight"])
    return pd.concat(frames, ignore_index=True)
