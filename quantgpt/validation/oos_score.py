"""Out-of-sample scoring helpers shared by factor and strategy results."""

from __future__ import annotations

import math
from typing import Any


def compute_oos_score(oos_result: dict, data_quality: dict | None = None) -> dict:
    """Compute a conservative OOS-first score and decision."""
    train_metrics = _metrics(oos_result, "train")
    valid_metrics = _metrics(oos_result, "valid")
    test_metrics = _metrics(oos_result, "test")
    decay = oos_result.get("decay", {}) if isinstance(oos_result, dict) else {}
    data_quality = data_quality or oos_result.get("data_quality") if isinstance(oos_result, dict) else data_quality

    train_score = _period_score(train_metrics)
    valid_score = _period_score(valid_metrics)
    test_score = _period_score(test_metrics)
    stability_score = _stability_score(decay)
    decay_penalty = _decay_penalty(decay)
    data_quality_penalty = _data_quality_penalty(data_quality)

    score = (
        train_score * 0.2
        + valid_score * 0.3
        + test_score * 0.4
        + stability_score * 0.1
        - data_quality_penalty
    )
    score = round(max(0.0, min(100.0, score)), 2)

    reasons = _decision_reasons(test_metrics, decay, data_quality)
    decision = "candidate"
    overfit_risk = "low"
    if _num(test_metrics.get("sharpe", test_metrics.get("long_short_sharpe"))) < 0.5:
        decision = "reject"
        overfit_risk = "high"
    if _num(test_metrics.get("ic_mean", test_metrics.get("direction_adjusted_rank_ic_mean"))) <= 0:
        decision = "reject"
        overfit_risk = "high"
    if any(_num(value) > 0.7 for value in decay.values() if value is not None):
        if decision != "reject":
            decision = "watchlist"
            overfit_risk = "medium-high"
    if data_quality_penalty >= 15:
        decision = "reject"
        overfit_risk = "high"
    elif data_quality_penalty > 0 and decision == "candidate":
        decision = "watchlist"
        overfit_risk = "medium"
    if score < 50 and decision == "candidate":
        decision = "watchlist"
        overfit_risk = "medium"

    return {
        "score": score,
        "decision": decision,
        "grade": _grade(score),
        "overfit_risk": overfit_risk,
        "train_score": round(train_score, 2),
        "valid_score": round(valid_score, 2),
        "test_score": round(test_score, 2),
        "stability_score": round(stability_score, 2),
        "decay_penalty": round(decay_penalty, 2),
        "data_quality_penalty": round(data_quality_penalty, 2),
        "reasons": reasons,
        "metrics_scope": "oos_train_valid_test",
    }


def _metrics(oos_result: dict, key: str) -> dict:
    value = oos_result.get(key, {}) if isinstance(oos_result, dict) else {}
    metrics = value.get("metrics", {}) if isinstance(value, dict) else {}
    return metrics if isinstance(metrics, dict) else {}


def _num(value: Any, default: float = 0.0) -> float:
    try:
        output = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(output) or math.isinf(output):
        return default
    return output


def _period_score(metrics: dict) -> float:
    sharpe = _num(metrics.get("sharpe", metrics.get("long_short_sharpe")))
    annual = _num(metrics.get("annual_return", metrics.get("long_short_annual")))
    max_drawdown = _num(metrics.get("max_drawdown"))
    turnover = _num(metrics.get("turnover"))
    ic = _num(metrics.get("ic_mean", metrics.get("direction_adjusted_rank_ic_mean")))
    score = 50.0
    score += min(25.0, max(-25.0, sharpe * 12.0))
    score += min(15.0, max(-15.0, annual * 35.0))
    score += min(15.0, max(-15.0, ic * 300.0))
    score += max(-15.0, max_drawdown * 80.0)
    score -= min(10.0, turnover * 5.0)
    return max(0.0, min(100.0, score))


def _stability_score(decay: dict) -> float:
    values = [_num(value) for value in decay.values() if value is not None]
    if not values:
        return 50.0
    penalty = sum(max(0.0, value) for value in values) / len(values) * 100
    return max(0.0, min(100.0, 100.0 - penalty))


def _decay_penalty(decay: dict) -> float:
    return max(0.0, 100.0 - _stability_score(decay))


def _data_quality_penalty(data_quality: dict | None) -> float:
    if not data_quality:
        return 0.0
    before = _num(data_quality.get("before_rows"))
    dropped = _num(data_quality.get("dropped_rows"))
    penalty = 0.0
    if before > 0:
        penalty += min(20.0, dropped / before * 100)
    warnings = data_quality.get("warnings", []) if isinstance(data_quality, dict) else []
    if warnings:
        penalty += min(10.0, len(warnings) * 2.0)
    if data_quality.get("adjustment") == "unknown":
        penalty += 3.0
    return penalty


def _decision_reasons(test_metrics: dict, decay: dict, data_quality: dict | None) -> list[str]:
    reasons = []
    test_sharpe = _num(test_metrics.get("sharpe", test_metrics.get("long_short_sharpe")))
    test_ic = _num(test_metrics.get("ic_mean", test_metrics.get("direction_adjusted_rank_ic_mean")))
    reasons.append(f"test Sharpe is {test_sharpe:.3f}")
    reasons.append(f"test IC is {test_ic:.4f}")
    for key in ("test_sharpe_decay", "test_ic_decay"):
        value = decay.get(key) if isinstance(decay, dict) else None
        if value is not None:
            reasons.append(f"{key} is {_num(value):.2%}")
    if data_quality:
        reasons.append(
            f"data quality dropped {int(_num(data_quality.get('dropped_rows')))} rows"
        )
        if data_quality.get("warnings"):
            reasons.append("data quality warnings are present")
    return reasons


def _grade(score: float) -> str:
    if score >= 80:
        return "A"
    if score >= 65:
        return "B"
    if score >= 50:
        return "C"
    return "D"
