"""Strategy-level validation summaries for Post-MVP review."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .result import StrategyBacktestResult


def run_strategy_anti_overfit(result: StrategyBacktestResult) -> dict:
    returns = result.strategy_returns.dropna().astype(float)
    tests = []
    tests.append(_test("positive_total_return", float((1 + returns).prod() - 1) > 0 if len(returns) else False))
    tests.append(_test("drawdown_within_limit", result.metrics.get("max_drawdown", 0.0) > -0.3))
    tests.append(_test("turnover_not_extreme", result.metrics.get("turnover", 0.0) <= 1.5))
    tests.append(_test("risk_logs_not_excessive", len(result.risk_logs) <= 10))

    passed_count = sum(1 for item in tests if item["passed"])
    return {
        "type": "strategy_anti_overfit",
        "passed": passed_count >= 3,
        "score": round(passed_count / len(tests) * 100, 2),
        "passed_count": passed_count,
        "total_count": len(tests),
        "tests": tests,
    }


def run_strategy_rolling_validation(result: StrategyBacktestResult, windows: int = 3) -> dict:
    returns = result.strategy_returns.dropna().astype(float)
    if returns.empty:
        return {
            "type": "strategy_rolling_validation",
            "passed": False,
            "windows": [],
            "summary": "no_returns",
        }

    windows = max(1, min(windows, len(returns)))
    chunks = [returns.iloc[indexes] for indexes in np.array_split(np.arange(len(returns)), windows)]
    rows = []
    for idx, chunk in enumerate(chunks, start=1):
        if len(chunk) == 0:
            continue
        rows.append({
            "window": idx,
            "start_date": pd.Timestamp(chunk.index.min()).strftime("%Y-%m-%d"),
            "end_date": pd.Timestamp(chunk.index.max()).strftime("%Y-%m-%d"),
            "total_return": float((1 + chunk).prod() - 1),
            "mean_return": float(chunk.mean()),
            "volatility": float(chunk.std()) if len(chunk) > 1 else 0.0,
        })
    positive = sum(1 for row in rows if row["total_return"] > 0)
    return {
        "type": "strategy_rolling_validation",
        "passed": positive >= max(1, len(rows) // 2),
        "positive_windows": positive,
        "total_windows": len(rows),
        "windows": rows,
    }


def _test(name: str, passed: bool) -> dict:
    return {"name": name, "passed": bool(passed)}
