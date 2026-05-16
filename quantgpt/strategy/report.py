"""Strategy report wrapper around the existing QuantStats report generator."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from ..report import generate_report
from .result import StrategyBacktestResult
from .score import compute_strategy_score


def generate_strategy_report(
    result: StrategyBacktestResult,
    benchmark_returns: pd.Series | None = None,
    output_dir: str | None = None,
) -> dict:
    """Generate an HTML strategy report and a lightweight summary JSON."""
    report_result = generate_report(
        result.strategy_returns,
        benchmark_returns=benchmark_returns,
        title=f"Strategy: {result.spec.name}",
        output_dir=output_dir,
        periods_per_year=252,
    )
    score = compute_strategy_score(result)
    summary = result.to_summary()
    summary["strategy_score"] = score

    report_path = Path(report_result["report_path"])
    summary_path = report_path.with_suffix(".summary.json")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    return {
        "report_path": str(report_path),
        "summary_json_path": str(summary_path),
        "metrics": report_result["metrics"],
        "strategy_score": score,
    }
