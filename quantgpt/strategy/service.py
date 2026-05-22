"""JSON-facing strategy service helpers shared by MCP and REST."""

from __future__ import annotations

import json

import pandas as pd

from ..validation.oos_score import compute_oos_score
from ..validation.promotion import BOUNDARY_EXPORT, assert_promotion_ready_for_boundary
from .adapters import list_data_fields as _list_data_fields
from .adapters import list_markets as _list_markets
from .backtest import StrategyBacktestRequest, run_strategy_backtest
from .diagnosis import diagnose_strategy_metrics, diagnose_strategy_result
from .export import export_strategy_candidate
from .optimizer import optimize_candidate_weights
from .report import generate_strategy_report
from .result import StrategyBacktestResult
from .score import compute_strategy_score_from_metrics
from .spec import parse_strategy_spec
from .templates import get_strategy_template, instantiate_strategy_template, list_strategy_templates
from .validation import run_strategy_anti_overfit, run_strategy_rolling_validation
from .validator import validate_strategy_spec as _validate_strategy_spec


def list_strategy_markets() -> dict:
    return {"markets": _list_markets()}


def list_strategy_data_fields(market: str = "a_share") -> dict:
    return {"market": market, "data_fields": _list_data_fields(market)}


def list_strategy_templates_payload() -> dict:
    return {"templates": list_strategy_templates()}


def get_strategy_template_payload(template_id: str) -> dict:
    return get_strategy_template(template_id)


def instantiate_strategy_template_payload(template_id: str, overrides: dict | None = None) -> dict:
    spec = instantiate_strategy_template(template_id, overrides=overrides)
    validation = validate_strategy_payload(spec)
    return {"spec": spec, "validation": validation}


def validate_strategy_payload(spec: dict) -> dict:
    result = _validate_strategy_spec(spec)
    payload = result.to_dict()
    if result.spec is not None:
        payload["spec"] = result.spec.model_dump()
    return payload


def run_strategy_backtest_payload(request_data: dict) -> dict:
    result = run_strategy_backtest(StrategyBacktestRequest.model_validate(request_data))
    payload = strategy_result_to_payload(result)
    if payload.get("oos_result"):
        payload["strategy_score"] = compute_oos_score(payload["oos_result"], payload.get("data_quality"))
    else:
        payload["strategy_score"] = compute_strategy_score_from_metrics(
            payload["metrics"],
            payload["risk_logs"],
            payload["validation_issues"],
            payload["diagnostics"],
        )
    return payload


def score_strategy_payload(result_payload: dict) -> dict:
    if result_payload.get("oos_result"):
        return compute_oos_score(result_payload["oos_result"], result_payload.get("data_quality"))
    return compute_strategy_score_from_metrics(
        result_payload.get("metrics", {}),
        result_payload.get("risk_logs", []),
        result_payload.get("validation_issues", []),
        result_payload.get("diagnostics", {}),
    )


def generate_strategy_report_payload(result_payload: dict, output_dir: str | None = None) -> dict:
    result = strategy_result_from_payload(result_payload)
    return generate_strategy_report(result, output_dir=output_dir)


def export_strategy_candidate_payload(result_payload: dict, output_dir: str | None = None) -> dict:
    assert_promotion_ready_for_boundary(result_payload.get("validation_provenance"), BOUNDARY_EXPORT)
    experiment_id = result_payload.get("experiment_id")
    factor_hash = result_payload.get("factor_hash")
    data_snapshot_id = result_payload.get("data_snapshot_id")
    if not experiment_id:
        raise ValueError("strategy_signal.v1 export requires experiment_id")
    if not factor_hash:
        raise ValueError("strategy_signal.v1 export requires factor_hash")
    if not data_snapshot_id:
        raise ValueError("strategy_signal.v1 export requires data_snapshot_id")
    result = strategy_result_from_payload(result_payload)
    payload = export_strategy_candidate(
        result,
        output_dir=output_dir,
        experiment_id=experiment_id,
        factor_hash=factor_hash,
        data_snapshot_id=data_snapshot_id,
        strategy_id=result_payload.get("strategy_id"),
        validation_summary=_export_validation_summary(result_payload),
    )
    payload["validation_provenance"] = result_payload["validation_provenance"]
    return payload


def diagnose_strategy_payload(result_payload: dict) -> dict:
    if "spec" in result_payload:
        return diagnose_strategy_result(strategy_result_from_payload(result_payload))
    return diagnose_strategy_metrics(
        result_payload.get("metrics", {}),
        result_payload.get("risk_logs", []),
        result_payload.get("validation_issues", []),
        result_payload.get("diagnostics", {}),
    )


def run_strategy_anti_overfit_payload(result_payload: dict) -> dict:
    return run_strategy_anti_overfit(strategy_result_from_payload(result_payload))


def run_strategy_rolling_validation_payload(result_payload: dict, windows: int = 3) -> dict:
    return run_strategy_rolling_validation(strategy_result_from_payload(result_payload), windows=windows)


def optimize_candidate_weights_payload(signals: list[dict], spec_payload: dict) -> dict:
    spec = parse_strategy_spec(spec_payload)
    return optimize_candidate_weights(signals, spec)


def _export_validation_summary(result_payload: dict) -> dict:
    oos_result = result_payload.get("oos_result") or {}
    return {
        "oos_enabled": bool(oos_result),
        "direction_policy": result_payload.get("direction_policy") or oos_result.get("direction_policy"),
        "train_period": _oos_period(oos_result, "train"),
        "validation_period": _oos_period(oos_result, "valid"),
        "test_period": _oos_period(oos_result, "test"),
        "promotion_gate_passed": True,
        "data_snapshot_id": result_payload.get("data_snapshot_id"),
    }


def _oos_period(oos_result: dict, stage: str) -> list | None:
    payload = oos_result.get(stage)
    if not isinstance(payload, dict):
        return None
    period = payload.get("period")
    return period if isinstance(period, list) else None


def strategy_result_to_payload(result: StrategyBacktestResult) -> dict:
    payload = result.to_summary()
    payload["spec"] = result.spec.model_dump()
    payload["strategy_returns"] = _series_to_records(result.strategy_returns)
    payload["target_weights"] = _frame_to_records(result.target_weights)
    payload["cash_weights"] = _frame_to_records(result.cash_weights)
    payload["turnover_by_rebalance"] = _frame_to_records(result.turnover_by_rebalance)
    payload["cost_by_rebalance"] = _frame_to_records(result.cost_by_rebalance)
    return payload


def strategy_result_from_payload(payload: dict) -> StrategyBacktestResult:
    spec = parse_strategy_spec(payload["spec"])
    returns = pd.Series(
        [row["value"] for row in payload.get("strategy_returns", [])],
        index=pd.to_datetime([row["date"] for row in payload.get("strategy_returns", [])]),
        name="strategy",
        dtype=float,
    )
    return StrategyBacktestResult(
        spec=spec,
        start_date=payload.get("start_date", ""),
        end_date=payload.get("end_date", ""),
        benchmark=payload.get("benchmark", "hs300"),
        strategy_returns=returns,
        target_weights=pd.DataFrame(payload.get("target_weights", [])),
        cash_weights=pd.DataFrame(payload.get("cash_weights", [])),
        turnover_by_rebalance=pd.DataFrame(payload.get("turnover_by_rebalance", [])),
        cost_by_rebalance=pd.DataFrame(payload.get("cost_by_rebalance", [])),
        risk_logs=payload.get("risk_logs", []),
        latest_holdings=payload.get("latest_holdings", []),
        metrics=payload.get("metrics", {}),
        validation_issues=payload.get("validation_issues", []),
        diagnostics=payload.get("diagnostics", {}),
        validation_mode=payload.get("validation_mode", "single_period"),
        direction_policy=payload.get("direction_policy"),
        data_quality=payload.get("data_quality"),
        data_snapshot_id=payload.get("data_snapshot_id"),
        data_source=payload.get("data_source"),
        data_source_metadata=payload.get("data_source_metadata"),
        oos_result=payload.get("oos_result"),
        oos_summary=payload.get("oos_summary"),
        oos_score=payload.get("oos_score") or payload.get("strategy_score"),
    )


def dumps(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def _series_to_records(series: pd.Series) -> list[dict]:
    return [
        {
            "date": pd.Timestamp(index).strftime("%Y-%m-%d"),
            "value": float(value),
        }
        for index, value in series.items()
    ]


def _frame_to_records(frame: pd.DataFrame) -> list[dict]:
    if frame is None or frame.empty:
        return []
    records = []
    for row in frame.to_dict(orient="records"):
        records.append({
            key: pd.Timestamp(value).strftime("%Y-%m-%d") if isinstance(value, pd.Timestamp) else value
            for key, value in row.items()
        })
    return records
