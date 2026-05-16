"""Strategy-level scoring for MVP results."""

from __future__ import annotations

from .diagnosis import diagnose_strategy_metrics
from .result import StrategyBacktestResult


def compute_strategy_score(result: StrategyBacktestResult) -> dict:
    """Compute a strategy score from strategy-level returns, weights, and risk logs."""
    return compute_strategy_score_from_metrics(
        result.metrics,
        result.risk_logs,
        result.validation_issues,
        result.diagnostics,
    )


def compute_strategy_score_from_metrics(
    metrics: dict,
    risk_logs: list[dict] | None = None,
    validation_issues: list[dict] | None = None,
    diagnostics: dict | None = None,
) -> dict:
    risk_logs = risk_logs or []
    validation_issues = validation_issues or []
    score = 50.0
    score += min(20.0, max(-20.0, metrics.get("sharpe", 0.0) * 8.0))
    score += min(15.0, max(-15.0, metrics.get("annual_return", 0.0) * 40.0))
    score += max(-15.0, metrics.get("max_drawdown", 0.0) * 60.0)
    score -= min(10.0, metrics.get("turnover", 0.0) * 5.0)
    score += min(10.0, max(-10.0, metrics.get("ic_ir", 0.0) * 3.0))
    score -= min(15.0, len(risk_logs) * 1.5)
    score = round(max(0.0, min(100.0, score)), 2)

    failure_reasons = []
    if metrics.get("sharpe", 0.0) <= 0:
        failure_reasons.append("NON_POSITIVE_SHARPE")
    if metrics.get("max_drawdown", 0.0) < -0.2:
        failure_reasons.append("LARGE_DRAWDOWN")
    if any(log.get("code") == "TURNOVER_LIMIT_REBALANCE_SKIPPED" for log in risk_logs):
        failure_reasons.append("TURNOVER_LIMIT_SKIPPED_REBALANCE")

    return {
        "score": score,
        "grade": _grade(score),
        "components": {
            "return_quality": metrics.get("annual_return", 0.0),
            "risk": metrics.get("max_drawdown", 0.0),
            "sharpe": metrics.get("sharpe", 0.0),
            "turnover": metrics.get("turnover", 0.0),
            "ic_ir": metrics.get("ic_ir", 0.0),
            "risk_log_count": len(risk_logs),
        },
        "failure_reasons": failure_reasons,
        "risk_logs": risk_logs,
        "validation_issues": validation_issues,
        "strategy_anti_overfit": (diagnostics or {}).get("strategy_anti_overfit", "not_run"),
        "strategy_rolling_validation": (diagnostics or {}).get("strategy_rolling_validation", "not_run"),
        "strategy_diagnosis": diagnose_strategy_metrics(metrics, risk_logs, validation_issues, diagnostics),
    }


def _grade(score: float) -> str:
    if score >= 80:
        return "A"
    if score >= 65:
        return "B"
    if score >= 50:
        return "C"
    return "D"
