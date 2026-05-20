"""Local OOS/data-quality gate before formal WQ BRAIN submission."""

from __future__ import annotations

import os
import traceback
from typing import Callable, Literal

from .anti_overfit import AntiOverfitDetector
from .data_quality import DataQualityConfig, run_data_quality_gate
from .fundamental_data import detect_fundamental_vars, enrich_market_data
from .market_data import MarketDataFetcher, get_universe
from .rolling_validator import run_rolling_validation
from .validation.oos_backtest import run_factor_oos_backtest, to_public_oos_result
from .validation.oos_score import compute_oos_score
from .validation.promotion import (
    BOUNDARY_SUBMIT,
    VALIDATION_SUITE_FAILED,
    build_factor_validation_provenance,
    evaluate_promotion_provenance,
    testcase_to_dict,
)
from .validation.split import OOSConfig

PreflightRunner = Callable[[str], dict]
AdjustmentMode = Literal["qfq", "hfq", "none", "unknown"]


def _default_adjustment() -> AdjustmentMode:
    value = os.environ.get("WQ_SUBMISSION_PREFLIGHT_ADJUSTMENT", "qfq").lower()
    if value in ("qfq", "hfq", "none", "unknown"):
        return value
    return "unknown"


DEFAULT_UNIVERSE = os.environ.get("WQ_SUBMISSION_PREFLIGHT_UNIVERSE", "hs300")
DEFAULT_START_DATE = os.environ.get("WQ_SUBMISSION_PREFLIGHT_START_DATE", "2018-01-01")
DEFAULT_END_DATE = os.environ.get("WQ_SUBMISSION_PREFLIGHT_END_DATE", "2025-12-31")
DEFAULT_N_GROUPS = int(os.environ.get("WQ_SUBMISSION_PREFLIGHT_N_GROUPS", "5"))
DEFAULT_HOLDING_PERIOD = int(os.environ.get("WQ_SUBMISSION_PREFLIGHT_HOLDING_PERIOD", "5"))
DEFAULT_ADJUSTMENT = _default_adjustment()
PASS_DECISIONS = {"candidate"}
LOCAL_PREFLIGHT_SCOPE_MISMATCH = "LOCAL_PREFLIGHT_SCOPE_MISMATCH"


def local_preflight_scope(universe: str = DEFAULT_UNIVERSE) -> dict:
    return {
        "engine": "local",
        "market": "a_share",
        "universe": universe,
    }


def wq_target_scope(
    *,
    region: str | None = None,
    universe: str | None = None,
    delay: int | None = None,
    neutralization: str | None = None,
) -> dict:
    scope: dict[str, object] = {
        "engine": "wq_brain",
        "instrument_type": "EQUITY",
    }
    if region is not None:
        scope["region"] = region
    if universe is not None:
        scope["universe"] = universe
    if delay is not None:
        scope["delay"] = delay
    if neutralization is not None:
        scope["neutralization"] = neutralization
    return scope


def _normalize_scope_value(value: object) -> object:
    if isinstance(value, str):
        return value.upper()
    return value


def _scope_matches(preflight_scope: dict | None, target_scope: dict | None) -> bool:
    if not target_scope:
        return True
    if not preflight_scope:
        return False
    if _normalize_scope_value(preflight_scope.get("engine")) != _normalize_scope_value(target_scope.get("engine")):
        return False

    engine = _normalize_scope_value(target_scope.get("engine"))
    comparable_keys = ("region", "market", "universe", "delay", "neutralization")
    if engine == "WQ_BRAIN":
        comparable_keys = ("region", "universe", "delay", "neutralization")

    for key in comparable_keys:
        target_value = target_scope.get(key)
        if target_value is None:
            continue
        if key not in preflight_scope:
            return False
        if _normalize_scope_value(preflight_scope.get(key)) != _normalize_scope_value(target_value):
            return False
    return True


def _scope_mismatch_result(expression: str | None, target_scope: dict, preflight_scope: dict) -> dict:
    return {
        "allowed": False,
        "status": "scope_mismatch",
        "error_code": LOCAL_PREFLIGHT_SCOPE_MISMATCH,
        "error": (
            "Local submission preflight uses A-share data and cannot authorize the requested "
            "WQ BRAIN submission scope. Provide submission_override_reason to submit based on "
            "remote WQ validation instead."
        ),
        "scope_mismatch": True,
        "preflight_scope": preflight_scope,
        "target_scope": target_scope,
        "params": {
            "expression": expression,
            "oos_enabled": True,
            "data_quality": True,
            "validation_suite": True,
        },
    }


def _apply_target_scope(result: dict, target_scope: dict | None) -> dict:
    if not target_scope:
        return result

    scoped = dict(result)
    scoped["target_scope"] = target_scope
    preflight_scope = scoped.get("preflight_scope")
    if _scope_matches(preflight_scope, target_scope):
        scoped["scope_mismatch"] = False
        return scoped

    scoped["scope_mismatch"] = True
    if scoped.get("allowed"):
        scoped["allowed"] = False
        scoped["status_before_scope_check"] = scoped.get("status")
        scoped["error_code_before_scope_check"] = scoped.get("error_code")
        scoped["status"] = "scope_mismatch"
        scoped["error_code"] = LOCAL_PREFLIGHT_SCOPE_MISMATCH
        scoped["error"] = (
            "Preflight scope does not match the requested WQ BRAIN submission scope. "
            "Provide submission_override_reason to submit based on remote WQ validation instead."
        )
    return scoped


def run_local_submission_preflight(
    expression: str,
    *,
    universe: str = DEFAULT_UNIVERSE,
    start_date: str = DEFAULT_START_DATE,
    end_date: str = DEFAULT_END_DATE,
    n_groups: int = DEFAULT_N_GROUPS,
    holding_period: int = DEFAULT_HOLDING_PERIOD,
    rebalance_anchor: str | None = None,
) -> dict:
    """Run the required local OOS + data-quality check for a WQ submission candidate."""
    preflight_scope = local_preflight_scope(universe)
    params = {
        "expression": expression,
        "universe": universe,
        "start_date": start_date,
        "end_date": end_date,
        "n_groups": n_groups,
        "holding_period": holding_period,
        "rebalance_anchor": rebalance_anchor,
        "oos_enabled": True,
        "data_quality": True,
        "data_quality_adjustment": DEFAULT_ADJUSTMENT,
        "rolling_window": True,
        "placebo_test": True,
        "time_shift_test": True,
        "validation_suite": "factor_validation/v1",
    }
    try:
        stock_codes = get_universe(universe, date=start_date)
        market_df = MarketDataFetcher().fetch_stocks(stock_codes, start_date, end_date)
        if market_df is None or len(market_df) == 0:
            return {
                "allowed": False,
                "status": "unavailable",
                "error_code": "LOCAL_PREFLIGHT_UNAVAILABLE",
                "error": "No local market data available for submission preflight",
                "preflight_scope": preflight_scope,
                "params": params,
            }

        market_df, data_quality_report = run_data_quality_gate(
            market_df,
            DataQualityConfig(enabled=True, mode="filter", adjustment=DEFAULT_ADJUSTMENT),
        )
        data_quality_report["mode"] = "filter"
        fund_vars = detect_fundamental_vars(expression)
        if fund_vars:
            market_df = enrich_market_data(market_df, fund_vars, stock_codes, start_date, end_date)
            missing_fundamentals = [
                name for name in fund_vars
                if name in market_df.columns and bool(market_df[name].isna().any())
            ]
            if missing_fundamentals:
                data_quality_report.setdefault("warnings", []).append(
                    "fundamental data contains missing values after enrichment; "
                    "submission preflight data_quality only gates base OHLCV market data"
                )

        result = run_factor_oos_backtest(
            market_df,
            expression,
            n_groups=n_groups,
            holding_period=holding_period,
            neutralize_industry=True,
            neutralize_cap=True,
            oos_config=OOSConfig(rebalance_anchor=rebalance_anchor),
            rebalance_anchor=rebalance_anchor,
        )
        public_oos = to_public_oos_result(result.get("oos_result", {}))
        public_oos["data_quality"] = data_quality_report
        oos_score = compute_oos_score(public_oos, data_quality=data_quality_report)
        factor_df = result.get("_direction_adjusted_factor_df")
        if factor_df is None:
            factor_df = result.get("_factor_df")
        rolling_validation = None
        placebo_test = None
        if factor_df is not None and len(factor_df) > 100:
            try:
                rolling_validation = run_rolling_validation(factor_df, holding_period)
            except Exception as exc:
                rolling_validation = {"score": 0.0, "windows": [], "summary": {"error": str(exc)}}
            try:
                placebo_test = testcase_to_dict(
                    AntiOverfitDetector(factor_df, holding_period).test_placebo()
                )
            except Exception as exc:
                placebo_test = {
                    "name": "安慰剂检验",
                    "passed": False,
                    "details": {"error": str(exc)},
                }

        validation_provenance = build_factor_validation_provenance(
            oos_result=public_oos,
            oos_score=oos_score,
            data_quality=data_quality_report,
            rolling_validation=rolling_validation,
            placebo_test=placebo_test,
            source="local_submission_preflight",
            scope=preflight_scope,
            params=params,
        )
        promotion_gate = evaluate_promotion_provenance(validation_provenance, BOUNDARY_SUBMIT)
        decision = oos_score.get("decision")
        allowed = decision in PASS_DECISIONS and promotion_gate["allowed"]
        return {
            "allowed": allowed,
            "status": "passed" if allowed else "failed",
            "error_code": None if allowed else promotion_gate.get("error_code") or VALIDATION_SUITE_FAILED,
            "decision": decision,
            "score": oos_score.get("score"),
            "grade": oos_score.get("grade"),
            "overfit_risk": oos_score.get("overfit_risk"),
            "required_decisions": sorted(PASS_DECISIONS),
            "preflight_scope": preflight_scope,
            "params": params,
            "oos_score": oos_score,
            "data_quality": data_quality_report,
            "rolling_validation": rolling_validation,
            "placebo_test": placebo_test,
            "promotion_gate": promotion_gate,
            "validation_provenance": validation_provenance,
            "oos_result": public_oos,
        }
    except Exception as exc:
        return {
            "allowed": False,
            "status": "unavailable",
            "error_code": "LOCAL_PREFLIGHT_UNAVAILABLE",
            "error": str(exc),
            "traceback": traceback.format_exc(limit=8),
            "preflight_scope": preflight_scope,
            "params": params,
        }


def unavailable_submission_preflight(reason: str, expression: str | None = None) -> dict:
    return {
        "allowed": False,
        "status": "unavailable",
        "error_code": "LOCAL_PREFLIGHT_UNAVAILABLE",
        "error": reason,
        "params": {
            "expression": expression,
            "oos_enabled": True,
            "data_quality": True,
            "validation_suite": True,
        },
    }


def require_submission_preflight(
    expression: str | None,
    *,
    override_reason: str | None = None,
    preflight_runner: PreflightRunner | None = None,
    unavailable_reason: str | None = None,
    target_scope: dict | None = None,
) -> dict:
    """Return a decision object; callers must only submit when ``allowed`` is true."""
    if not expression:
        result = unavailable_submission_preflight(
            unavailable_reason or "No expression provenance available for local submission preflight",
            expression,
        )
    elif preflight_runner is None and target_scope and not _scope_matches(local_preflight_scope(), target_scope):
        result = _scope_mismatch_result(expression, target_scope, local_preflight_scope())
    else:
        runner = preflight_runner or run_local_submission_preflight
        result = runner(expression)
        result = _apply_target_scope(result, target_scope)

    if result.get("allowed"):
        promotion_gate = evaluate_promotion_provenance(result.get("validation_provenance"), BOUNDARY_SUBMIT)
        if not promotion_gate["allowed"]:
            blocked = dict(result)
            blocked["allowed"] = False
            blocked["status"] = "failed"
            blocked["error_code"] = promotion_gate["error_code"]
            blocked["promotion_gate"] = promotion_gate
            blocked["error"] = "Submission requires factor_validation/v1 promotion provenance."
            result = blocked

    if result.get("allowed"):
        return result

    reason = (override_reason or "").strip()
    if reason:
        waived = dict(result)
        waived["allowed"] = True
        waived["status"] = "waived"
        waived["waived"] = True
        waived["original_status"] = result.get("status")
        waived["original_error_code"] = result.get("error_code")
        waived["override_reason"] = reason
        return waived
    return result
