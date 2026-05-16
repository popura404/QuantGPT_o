"""Strategy diagnosis taxonomy and suggested spec-level fixes."""

from __future__ import annotations

from .result import StrategyBacktestResult


def diagnose_strategy_result(result: StrategyBacktestResult) -> dict:
    return diagnose_strategy_metrics(
        result.metrics,
        result.risk_logs,
        result.validation_issues,
        result.diagnostics,
    )


def diagnose_strategy_metrics(
    metrics: dict,
    risk_logs: list[dict] | None = None,
    validation_issues: list[dict] | None = None,
    diagnostics: dict | None = None,
) -> dict:
    risk_logs = risk_logs or []
    validation_issues = validation_issues or []
    diagnostics = diagnostics or {}
    items = []

    if metrics.get("sharpe", 0.0) <= 0 or metrics.get("annual_return", 0.0) <= 0:
        items.append(_item(
            "RETURN_WEAK",
            "major",
            "Strategy return quality is weak.",
            ["Review factor direction, lower the selection breadth, or replace the weakest factor."],
        ))
    if metrics.get("max_drawdown", 0.0) < -0.2:
        items.append(_item(
            "DRAWDOWN_HIGH",
            "major",
            "Maximum drawdown is above the default review threshold.",
            ["Reduce max_asset_weight, add a defensive factor, or shorten rebalance_period."],
        ))
    if metrics.get("turnover", 0.0) > 1.0:
        items.append(_item(
            "TURNOVER_HIGH",
            "minor",
            "Average rebalance turnover is high.",
            ["Increase rebalance_period or set a lower max_turnover cap."],
        ))
    if any(log.get("code") == "MAX_ASSET_WEIGHT_CLIPPED" for log in risk_logs):
        items.append(_item(
            "CONCENTRATION_CLIPPED",
            "minor",
            "Risk rules clipped one or more requested positions.",
            ["Use top_n with a larger count or lower max_asset_weight."],
        ))
    if any(log.get("code") == "TURNOVER_LIMIT_REBALANCE_SKIPPED" for log in risk_logs):
        items.append(_item(
            "TURNOVER_LIMIT_SKIPPED",
            "major",
            "A rebalance was skipped by max_turnover.",
            ["Raise max_turnover or increase rebalance_period to reduce churn."],
        ))
    if validation_issues:
        items.append(_item(
            "VALIDATION_ISSUES_PRESENT",
            "blocker",
            "Strategy validation produced one or more issues.",
            ["Fix validation_issues before treating this candidate as review-ready."],
        ))

    anti = diagnostics.get("strategy_anti_overfit")
    if isinstance(anti, dict) and not anti.get("passed", True):
        items.append(_item(
            "ANTI_OVERFIT_FAILED",
            "major",
            "Strategy anti-overfit checks did not pass.",
            ["Prefer simpler factors and verify performance across subperiods."],
        ))
    rolling = diagnostics.get("strategy_rolling_validation")
    if isinstance(rolling, dict) and not rolling.get("passed", True):
        items.append(_item(
            "ROLLING_VALIDATION_FAILED",
            "major",
            "Rolling validation shows unstable subperiod behavior.",
            ["Inspect the weakest window and avoid overfitting to the full sample."],
        ))

    return {
        "diagnoses": items,
        "summary": "pass" if not items else _worst_severity(items),
        "suggested_spec_changes": _suggested_spec_changes(items),
    }


def _item(code: str, severity: str, message: str, suggestions: list[str]) -> dict:
    return {
        "code": code,
        "severity": severity,
        "message": message,
        "suggested_actions": suggestions,
    }


def _worst_severity(items: list[dict]) -> str:
    order = {"blocker": 3, "major": 2, "minor": 1}
    return max(items, key=lambda item: order.get(item["severity"], 0))["severity"]


def _suggested_spec_changes(items: list[dict]) -> list[dict]:
    codes = {item["code"] for item in items}
    changes = []
    if "TURNOVER_HIGH" in codes or "TURNOVER_LIMIT_SKIPPED" in codes:
        changes.append({"path": "portfolio_rule.rebalance_period", "action": "increase"})
    if "DRAWDOWN_HIGH" in codes or "CONCENTRATION_CLIPPED" in codes:
        changes.append({"path": "risk_rules.max_asset_weight", "action": "decrease"})
    if "RETURN_WEAK" in codes:
        changes.append({"path": "factors", "action": "review_direction_or_replace_factor"})
    return changes
