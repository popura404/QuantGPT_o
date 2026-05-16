"""JSON-facing strategy service helpers shared by MCP and REST."""

from __future__ import annotations

import json

import pandas as pd

from .adapters import list_data_fields as _list_data_fields
from .adapters import list_markets as _list_markets
from .backtest import StrategyBacktestRequest, run_strategy_backtest
from .report import generate_strategy_report
from .result import StrategyBacktestResult
from .score import compute_strategy_score_from_metrics
from .spec import parse_strategy_spec
from .validator import validate_strategy_spec as _validate_strategy_spec


def list_strategy_markets() -> dict:
    return {"markets": _list_markets()}


def list_strategy_data_fields(market: str = "a_share") -> dict:
    return {"market": market, "data_fields": _list_data_fields(market)}


def validate_strategy_payload(spec: dict) -> dict:
    result = _validate_strategy_spec(spec)
    payload = result.to_dict()
    if result.spec is not None:
        payload["spec"] = result.spec.model_dump()
    return payload


def run_strategy_backtest_payload(request_data: dict) -> dict:
    result = run_strategy_backtest(StrategyBacktestRequest.model_validate(request_data))
    payload = strategy_result_to_payload(result)
    payload["strategy_score"] = compute_strategy_score_from_metrics(
        payload["metrics"],
        payload["risk_logs"],
        payload["validation_issues"],
    )
    return payload


def score_strategy_payload(result_payload: dict) -> dict:
    return compute_strategy_score_from_metrics(
        result_payload.get("metrics", {}),
        result_payload.get("risk_logs", []),
        result_payload.get("validation_issues", []),
    )


def generate_strategy_report_payload(result_payload: dict, output_dir: str | None = None) -> dict:
    result = strategy_result_from_payload(result_payload)
    return generate_strategy_report(result, output_dir=output_dir)


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
