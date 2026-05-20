"""Train-fixed out-of-sample factor validation."""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Any, cast

import numpy as np
import pandas as pd

from quantgpt.backtest import _calc_max_drawdown, run_factor_backtest

from .split import OOSConfig, split_by_dates


def _plain_number(value: Any) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, (int, float)):
        if math.isnan(float(value)) or math.isinf(float(value)):
            return None
        return float(value)
    return value


def safe_decay(train_value, sample_value, warnings: list[str], name: str) -> float | None:
    train = _plain_number(train_value)
    sample = _plain_number(sample_value)
    if train is None or sample is None:
        warnings.append(f"{name} decay is unavailable because a metric is missing or NaN")
        return None
    if float(train) <= 0:
        warnings.append(f"{name} decay is unavailable because the training metric is not positive")
        return None
    return float(1 - (float(sample) / float(train)))


def _slice_series(series: pd.Series | None, window: dict) -> pd.Series:
    if series is None:
        return pd.Series(dtype=float)
    s = series.copy()
    s.index = pd.to_datetime(s.index)
    return s[(s.index >= pd.Timestamp(window["start"])) & (s.index <= pd.Timestamp(window["end"]))]


def _turnover_for_window(result: dict, window: dict) -> float:
    holdings = result.get("_selected_group_holdings") or {}
    if len(holdings) < 2:
        return 0.0
    start = pd.Timestamp(window["start"])
    end = pd.Timestamp(window["end"])
    dates = sorted(pd.Timestamp(d) for d in holdings)
    turnovers = []
    for i in range(1, len(dates)):
        current = dates[i]
        if current < start or current > end:
            continue
        prev_set = set(holdings.get(dates[i - 1], set()))
        curr_set = set(holdings.get(current, set()))
        avg_size = (len(prev_set) + len(curr_set)) / 2
        if avg_size == 0:
            turnovers.append(0.0)
            continue
        turnovers.append((len(curr_set - prev_set) + len(prev_set - curr_set)) / avg_size)
    if not turnovers:
        return 0.0
    holding_period = max(1, int(result.get("holding_period", 1)))
    return float(np.mean(turnovers) / holding_period)


def _metrics_for_window(result: dict, window: dict, trading_days_per_year: int, warnings: list[str], label: str) -> dict:
    strategy = _slice_series(result.get("strategy_returns"), window)
    ls_returns = _slice_series(result.get("ls_returns"), window)
    rank_ic = _slice_series(result.get("_direction_adjusted_rank_ic_series"), window)
    raw_rank_ic = _slice_series(result.get("_raw_rank_ic_series"), window)

    if strategy.empty:
        warnings.append(f"{label} strategy_returns is empty after evaluation-window masking")
    strat_std = float(strategy.std()) if len(strategy) else 0.0
    ls_std = float(ls_returns.std()) if len(ls_returns) else 0.0
    ic_std = float(rank_ic.std()) if len(rank_ic) else 0.0
    mean_strategy = float(strategy.mean()) if len(strategy) else 0.0
    mean_ls = float(ls_returns.mean()) if len(ls_returns) else 0.0
    annualize = math.sqrt(trading_days_per_year)

    rank_ic_mean = float(rank_ic.mean()) if len(rank_ic) else 0.0
    raw_rank_ic_mean = float(raw_rank_ic.mean()) if len(raw_rank_ic) else 0.0

    return {
        "strategy_days": int(len(strategy)),
        "strategy_mean": mean_strategy,
        "top_group_sharpe": float(mean_strategy / strat_std * annualize) if strat_std > 0 else 0.0,
        "long_short_sharpe": float(mean_ls / ls_std * annualize) if ls_std > 0 else 0.0,
        "long_short_annual": float((1 + mean_ls) ** trading_days_per_year - 1) if len(ls_returns) else 0.0,
        "max_drawdown": float(_calc_max_drawdown(strategy)) if len(strategy) else 0.0,
        "turnover": _turnover_for_window(result, window),
        "turnover_source": "selected_group_holdings_eval_mask",
        "raw_rank_ic_mean": raw_rank_ic_mean,
        "direction_adjusted_rank_ic_mean": rank_ic_mean,
        "ic_ir": float(rank_ic_mean / ic_std) if ic_std > 0 else 0.0,
    }


def _risk_from_decay(decay: dict, test_metrics: dict) -> str:
    test_sharpe = test_metrics.get("long_short_sharpe", 0.0)
    test_ic = test_metrics.get("direction_adjusted_rank_ic_mean", 0.0)
    high_decay = [
        value for value in decay.values()
        if isinstance(value, (int, float)) and value is not None and value > 0.7
    ]
    if test_sharpe < 0 or test_ic <= 0:
        return "high"
    if high_decay or test_sharpe < 0.5:
        return "medium"
    return "low"


def _json_safe(value):
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items() if not str(k).startswith("_")}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, pd.Series):
        out = []
        for idx, item in value.items():
            date = pd.Timestamp(idx).strftime("%Y-%m-%d") if not isinstance(idx, (int, str)) else str(idx)
            out.append({"date": date, "value": _json_safe(item)})
        return out
    if isinstance(value, pd.DataFrame):
        return [_json_safe(row) for row in value.to_dict(orient="records")]
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, np.generic):
        return _json_safe(value.item())
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def to_public_oos_result(result: dict) -> dict:
    """Drop private artifacts and convert pandas/numpy values to JSON-safe values."""
    return cast(dict, _json_safe(result))


def run_factor_oos_backtest(
    market_df: pd.DataFrame,
    expression: str,
    n_groups: int = 5,
    holding_period: int = 5,
    cost_rate: float = 0.003,
    neutralize_industry: bool = True,
    neutralize_cap: bool = True,
    oos_config: OOSConfig | None = None,
    rebalance_anchor: str | None = None,
    trading_days_per_year: int = 252,
    direction_mode: str = "auto_full",
    fixed_direction: int | None = None,
    evaluation_stage: str = "final",
) -> dict:
    """Run train-fixed OOS validation for a single factor expression."""
    if direction_mode != "auto_full" or fixed_direction is not None:
        raise ValueError("oos_enabled=true always uses train_fixed direction; do not pass fixed_direction")
    if evaluation_stage not in {"selection", "final"}:
        raise ValueError("evaluation_stage must be 'selection' or 'final'")

    config = oos_config or OOSConfig()
    if rebalance_anchor is not None:
        config = replace(config, rebalance_anchor=rebalance_anchor)

    split = split_by_dates(
        market_df,
        config,
        expression=expression,
        holding_period=holding_period,
    )
    anchor = split["rebalance_anchor"]
    warnings = list(split.get("warnings") or [])

    common_kwargs = {
        "n_groups": n_groups,
        "holding_period": holding_period,
        "cost_rate": cost_rate,
        "neutralize_industry": neutralize_industry,
        "neutralize_cap": neutralize_cap,
        "trading_days_per_year": trading_days_per_year,
        "rebalance_anchor": anchor,
    }
    train_result = run_factor_backtest(
        split["frames"]["train"],
        expression=expression,
        direction_mode="auto_full",
        **common_kwargs,
    )
    train_direction = -1 if train_result.get("flipped") else 1

    valid_result = run_factor_backtest(
        split["frames"]["valid"],
        expression=expression,
        direction_mode="fixed",
        fixed_direction=train_direction,
        **common_kwargs,
    )
    test_result = None
    if evaluation_stage == "final":
        test_result = run_factor_backtest(
            split["frames"]["test"],
            expression=expression,
            direction_mode="fixed",
            fixed_direction=train_direction,
            **common_kwargs,
        )

    train_metrics = _metrics_for_window(
        train_result, split["eval_windows"]["train"], trading_days_per_year, warnings, "train"
    )
    valid_metrics = _metrics_for_window(
        valid_result, split["eval_windows"]["valid"], trading_days_per_year, warnings, "valid"
    )
    test_window = split["eval_windows"]["test"]
    if test_result is not None:
        test_metrics = _metrics_for_window(
            test_result, test_window, trading_days_per_year, warnings, "test"
        )
    else:
        test_metrics = {}

    decay = {
        "valid_sharpe_decay": safe_decay(
            train_metrics.get("long_short_sharpe"), valid_metrics.get("long_short_sharpe"), warnings, "valid sharpe"
        ),
        "valid_ic_decay": safe_decay(
            train_metrics.get("direction_adjusted_rank_ic_mean"),
            valid_metrics.get("direction_adjusted_rank_ic_mean"),
            warnings,
            "valid rank IC",
        ),
    }
    if test_result is not None:
        decay["test_sharpe_decay"] = safe_decay(
            train_metrics.get("long_short_sharpe"), test_metrics.get("long_short_sharpe"), warnings, "test sharpe"
        )
        decay["test_ic_decay"] = safe_decay(
            train_metrics.get("direction_adjusted_rank_ic_mean"),
            test_metrics.get("direction_adjusted_rank_ic_mean"),
            warnings,
            "test rank IC",
        )
    else:
        warnings.append("final test was not run because evaluation_stage=selection")

    oos_result = {
        "oos_enabled": True,
        "direction_policy": "train_fixed",
        "evaluation_stage": evaluation_stage,
        "direction_basis": "cost_adjusted_group_mean",
        "direction_source": "train",
        "train_direction_source": train_result.get("direction_source"),
        "fixed_direction": train_direction,
        "rebalance_anchor": anchor,
        "resolved_warmup_days": split["resolved_warmup_days"],
        "report_scope": "oos_train_valid_test",
        "train": {
            "period": [split["eval_windows"]["train"]["start"], split["eval_windows"]["train"]["end"]],
            "metrics": train_metrics,
        },
        "valid": {
            "period": [split["eval_windows"]["valid"]["start"], split["eval_windows"]["valid"]["end"]],
            "metrics": valid_metrics,
        },
        "test": {
            "period": [test_window["start"], test_window["end"]],
            "metrics": test_metrics,
        },
        "decay": decay,
        "oos_risk": _risk_from_decay(decay, test_metrics if test_result is not None else valid_metrics),
        "warnings": warnings,
        "_train_result": train_result,
        "_valid_result": valid_result,
    }
    if test_result is not None:
        oos_result["_test_result"] = test_result
    else:
        oos_result["test"].update({
            "status": "withheld",
            "reason": "final_test_only",
        })
        oos_result["report_scope"] = "oos_train_valid_selection"
        oos_result["final_test_policy"] = "withheld_until_validation_stage_final"

    compatibility = dict(test_result if test_result is not None else valid_result)
    compatibility["holding_period"] = holding_period
    compatibility["oos_result"] = oos_result
    compatibility["direction_policy"] = "train_fixed"
    compatibility["report_scope"] = "oos_train_valid_test" if test_result is not None else "oos_train_valid_selection"
    if test_result is not None:
        compatibility["compatibility_warning"] = (
            "Top-level metrics are legacy-compatible final-test output; "
            "use oos_result for authoritative OOS research conclusions."
        )
    else:
        compatibility["compatibility_warning"] = (
            "Top-level metrics are valid-window selection output; "
            "final test metrics are withheld until evaluation_stage=final."
        )
    return compatibility
