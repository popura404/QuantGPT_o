"""Local OOS/data-quality gate before formal WQ BRAIN submission."""

from __future__ import annotations

import os
import traceback
from typing import Callable

from .data_quality import DataQualityConfig, run_data_quality_gate
from .fundamental_data import detect_fundamental_vars, enrich_market_data
from .market_data import MarketDataFetcher, get_universe
from .validation.oos_backtest import run_factor_oos_backtest, to_public_oos_result
from .validation.oos_score import compute_oos_score
from .validation.split import OOSConfig

PreflightRunner = Callable[[str], dict]

DEFAULT_UNIVERSE = os.environ.get("WQ_SUBMISSION_PREFLIGHT_UNIVERSE", "hs300")
DEFAULT_START_DATE = os.environ.get("WQ_SUBMISSION_PREFLIGHT_START_DATE", "2023-01-01")
DEFAULT_END_DATE = os.environ.get("WQ_SUBMISSION_PREFLIGHT_END_DATE", "2025-12-31")
DEFAULT_N_GROUPS = int(os.environ.get("WQ_SUBMISSION_PREFLIGHT_N_GROUPS", "5"))
DEFAULT_HOLDING_PERIOD = int(os.environ.get("WQ_SUBMISSION_PREFLIGHT_HOLDING_PERIOD", "5"))
PASS_DECISIONS = {"candidate"}


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
                "params": params,
            }

        market_df, data_quality_report = run_data_quality_gate(
            market_df,
            DataQualityConfig(enabled=True, mode="filter"),
        )
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
        decision = oos_score.get("decision")
        allowed = decision in PASS_DECISIONS
        return {
            "allowed": allowed,
            "status": "passed" if allowed else "failed",
            "error_code": None if allowed else "LOCAL_PREFLIGHT_FAILED",
            "decision": decision,
            "score": oos_score.get("score"),
            "grade": oos_score.get("grade"),
            "overfit_risk": oos_score.get("overfit_risk"),
            "required_decisions": sorted(PASS_DECISIONS),
            "params": params,
            "oos_score": oos_score,
            "data_quality": data_quality_report,
            "oos_result": public_oos,
        }
    except Exception as exc:
        return {
            "allowed": False,
            "status": "unavailable",
            "error_code": "LOCAL_PREFLIGHT_UNAVAILABLE",
            "error": str(exc),
            "traceback": traceback.format_exc(limit=8),
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
        },
    }


def require_submission_preflight(
    expression: str | None,
    *,
    override_reason: str | None = None,
    preflight_runner: PreflightRunner | None = None,
    unavailable_reason: str | None = None,
) -> dict:
    """Return a decision object; callers must only submit when ``allowed`` is true."""
    if not expression:
        result = unavailable_submission_preflight(
            unavailable_reason or "No expression provenance available for local submission preflight",
            expression,
        )
    else:
        runner = preflight_runner or run_local_submission_preflight
        result = runner(expression)

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
