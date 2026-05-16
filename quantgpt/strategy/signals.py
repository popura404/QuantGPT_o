"""Signal construction for StrategySpec strategies."""

from __future__ import annotations

import math

import pandas as pd

from .spec import StrategySpecV0, StrategySpecV1


def build_rank_threshold_signals(factor_frame: pd.DataFrame, spec: StrategySpecV0 | StrategySpecV1) -> pd.DataFrame:
    """Build score/action/eligibility signals from factor values only."""
    required = {"trade_date", "stock_code", "factor_value"}
    missing = required - set(factor_frame.columns)
    if missing:
        raise ValueError(f"factor_frame missing columns: {sorted(missing)}")

    direction = spec.factors[0].direction if spec.schema_version == "strategy_spec/v0" else "higher_is_better"
    frames = []
    for trade_date, group in factor_frame.dropna(subset=["factor_value"]).groupby("trade_date", sort=True):
        ascending = direction == "lower_is_better"
        ordered = group.sort_values(["factor_value", "stock_code"], ascending=[ascending, True]).copy()
        count = len(ordered)
        if count == 0:
            continue
        selected_count = _selected_count(count, spec)
        ordered["signal_rank"] = range(1, count + 1)
        ordered["score"] = (count - ordered["signal_rank"] + 1) / count
        ordered["eligibility"] = ordered["signal_rank"] <= selected_count
        ordered["action_hint"] = ordered["eligibility"].map(lambda eligible: "buy" if eligible else "hold")
        ordered["trade_date"] = trade_date
        frames.append(ordered[["trade_date", "stock_code", "factor_value", "score", "action_hint", "eligibility"]])

    if not frames:
        return pd.DataFrame(columns=["trade_date", "stock_code", "factor_value", "score", "action_hint", "eligibility"])
    return pd.concat(frames, ignore_index=True)


def _selected_count(count: int, spec: StrategySpecV0 | StrategySpecV1) -> int:
    top_n = getattr(spec.signal_rules, "top_n", None)
    if top_n is not None:
        return max(1, min(count, int(top_n)))
    long_quantile = spec.signal_rules.long_quantile
    return max(1, int(math.ceil(count * long_quantile)))
