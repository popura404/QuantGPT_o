"""Out-of-sample date splitting for factor validation."""

from __future__ import annotations

from dataclasses import dataclass, replace
from math import floor
from typing import Literal, cast

import pandas as pd

from ..expression_parser import infer_expression_lookback


@dataclass(frozen=True)
class OOSConfig:
    enabled: bool = True
    method: Literal["date_ratio", "date_cut"] = "date_ratio"

    train_ratio: float = 0.6
    valid_ratio: float = 0.2
    test_ratio: float = 0.2

    train_end: str | None = None
    valid_end: str | None = None

    min_train_days: int = 252
    min_valid_days: int = 126
    min_test_days: int = 126

    rebalance_anchor: str | None = None
    warmup_days: int | None = None


def infer_warmup_days(expression: str, holding_period: int) -> tuple[int, list[str]]:
    """Compose nested AST dependencies, preserving the legacy padding convention.

    Warmup allocation counts the complete observation requirement (one extra
    prior session for finite windows), with the existing holding-period floor.
    The evaluator still emits partial windows; these rows are not scoring rows.
    """
    try:
        lookback = infer_expression_lookback(expression)
    except ValueError:
        fallback = max(holding_period, 252)
        return fallback, [
            "warm-up lookback could not be inferred precisely; "
            f"using conservative {fallback} trading days"
        ]
    warnings = list(lookback.warnings)
    if lookback.recursive:
        return max(lookback.required_observations, holding_period, 252), warnings
    if lookback.prior_sessions:
        return max(lookback.required_observations, holding_period), warnings
    return 0, warnings


def _resolve_warmup(
    config: OOSConfig,
    *,
    expression: str | None,
    holding_period: int | None,
    resolved_warmup_days: int | None,
) -> tuple[int, list[str]]:
    warnings: list[str] = []
    if resolved_warmup_days is not None:
        warmup = int(resolved_warmup_days)
    elif config.warmup_days is not None:
        warmup = int(config.warmup_days)
    else:
        if expression is None or holding_period is None:
            raise ValueError("split_by_dates requires expression and holding_period when warmup_days is not supplied")
        warmup, warnings = infer_warmup_days(expression, holding_period)

    if warmup < 0:
        raise ValueError("warmup_days must be >= 0")
    if warmup == 0 and expression is not None and holding_period is not None:
        inferred, _ = infer_warmup_days(expression, holding_period)
        if inferred > 0:
            raise ValueError("warmup_days=0 is only allowed for expressions without time-series lookback")
    return warmup, warnings


def _validate_window_lengths(train_dates, valid_dates, test_dates, config: OOSConfig) -> None:
    if len(train_dates) < config.min_train_days:
        raise ValueError(f"train window has {len(train_dates)} days, below min_train_days={config.min_train_days}")
    if len(valid_dates) < config.min_valid_days:
        raise ValueError(f"validation window has {len(valid_dates)} days, below min_valid_days={config.min_valid_days}")
    if len(test_dates) < config.min_test_days:
        raise ValueError(f"test window has {len(test_dates)} days, below min_test_days={config.min_test_days}")


def _date_str(value) -> str:
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def split_by_dates(
    market_df: pd.DataFrame,
    config: OOSConfig,
    *,
    expression: str | None = None,
    holding_period: int | None = None,
    resolved_warmup_days: int | None = None,
) -> dict:
    """Split market data into train / valid / test frames by unique trade dates."""
    if "trade_date" not in market_df.columns:
        raise ValueError("market_df must contain trade_date")
    if config.method not in {"date_ratio", "date_cut"}:
        raise ValueError("OOSConfig.method must be 'date_ratio' or 'date_cut'")

    if abs((config.train_ratio + config.valid_ratio + config.test_ratio) - 1.0) > 1e-6:
        raise ValueError("train_ratio + valid_ratio + test_ratio must equal 1.0")

    warmup_days, warnings = _resolve_warmup(
        config,
        expression=expression,
        holding_period=holding_period,
        resolved_warmup_days=resolved_warmup_days,
    )

    df = market_df.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.sort_values(["trade_date", "stock_code"] if "stock_code" in df.columns else ["trade_date"])
    dates = pd.DatetimeIndex(sorted(pd.to_datetime(df["trade_date"].dropna().unique())))
    calendar_sessions = market_df.attrs.get("calendar_sessions", dates.tolist())
    if len(dates) < 3:
        raise ValueError("not enough unique trade_date values for train/valid/test split")

    if config.method == "date_ratio":
        train_n = floor(len(dates) * config.train_ratio)
        valid_n = floor(len(dates) * config.valid_ratio)
        train_dates = dates[:train_n]
        valid_dates = dates[train_n:train_n + valid_n]
        test_dates = dates[train_n + valid_n:]
    else:
        if config.train_end is None or config.valid_end is None:
            raise ValueError("date_cut requires train_end and valid_end")
        train_end = pd.Timestamp(config.train_end)
        valid_end = pd.Timestamp(config.valid_end)
        if train_end >= valid_end:
            raise ValueError("train_end must be earlier than valid_end")
        train_dates = dates[dates <= train_end]
        valid_dates = dates[(dates > train_end) & (dates <= valid_end)]
        test_dates = dates[dates > valid_end]

    _validate_window_lengths(train_dates, valid_dates, test_dates, config)

    date_positions = {pd.Timestamp(d): idx for idx, d in enumerate(dates)}

    def _frame_for(window_dates: pd.Index) -> tuple[pd.DataFrame, pd.Series]:
        start_pos = date_positions[cast(pd.Timestamp, window_dates[0])]
        warm_start_pos = max(0, start_pos - warmup_days)
        warm_dates = dates[warm_start_pos:date_positions[cast(pd.Timestamp, window_dates[-1])] + 1].tolist()
        eval_dates = window_dates.tolist()
        frame = cast(pd.DataFrame, df[df["trade_date"].isin(warm_dates)]).copy()
        frame.attrs["calendar_sessions"] = calendar_sessions
        mask = cast(pd.Series, frame["trade_date"]).isin(eval_dates)
        return frame, pd.Series(mask.to_numpy(dtype=bool), index=frame.index)

    train_frame, train_mask = _frame_for(train_dates)
    valid_frame, valid_mask = _frame_for(valid_dates)
    test_frame, test_mask = _frame_for(test_dates)

    rebalance_anchor = config.rebalance_anchor or _date_str(dates[0])
    resolved_config = replace(config, rebalance_anchor=rebalance_anchor, warmup_days=warmup_days)

    return {
        "config": resolved_config,
        "frames": {
            "train": train_frame,
            "valid": valid_frame,
            "test": test_frame,
        },
        "eval_windows": {
            "train": {"start": _date_str(train_dates[0]), "end": _date_str(train_dates[-1])},
            "valid": {"start": _date_str(valid_dates[0]), "end": _date_str(valid_dates[-1])},
            "test": {"start": _date_str(test_dates[0]), "end": _date_str(test_dates[-1])},
        },
        "eval_masks": {
            "train": train_mask,
            "valid": valid_mask,
            "test": test_mask,
        },
        "rebalance_anchor": rebalance_anchor,
        "resolved_warmup_days": warmup_days,
        "calendar_sessions": calendar_sessions,
        "warnings": warnings,
    }
