"""Candidate strategy signal export helpers."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .result import StrategyBacktestResult
from .schema import (
    FORBIDDEN_EXECUTION_FIELDS,
    NON_EXECUTION_NOTICE,
    STRATEGY_SIGNAL_V1,
    assert_no_forbidden_execution_fields,
    validate_strategy_signal_v1,
)

NON_LIVE_NOTICE = NON_EXECUTION_NOTICE
FORBIDDEN_EXPORT_KEYS = FORBIDDEN_EXECUTION_FIELDS


def export_strategy_candidate(
    result: StrategyBacktestResult,
    output_dir: str | None = None,
    *,
    experiment_id: str | None = None,
    factor_hash: str | None = None,
    data_snapshot_id: str | None = None,
    strategy_id: str | None = None,
    validation_summary: dict | None = None,
) -> dict:
    rows = _export_rows(result)
    as_of = rows[0]["trade_date"] if rows else result.end_date
    payload = {
        "schema_version": STRATEGY_SIGNAL_V1,
        "strategy_id": strategy_id or _safe_name(result.spec.name),
        "strategy_version": result.spec.schema_version,
        "experiment_id": experiment_id,
        "factor_hash": factor_hash,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "as_of": as_of,
        "market": result.spec.market,
        "asset_class": result.spec.asset_class,
        "universe": result.spec.universe,
        "rebalance_frequency": "daily",
        "holding_period": result.spec.portfolio_rule.rebalance_period,
        "signal_type": "candidate_rebalance_signal",
        "notice": NON_LIVE_NOTICE,
        "validation_summary": validation_summary or _validation_summary(result, data_snapshot_id=data_snapshot_id),
        "risk_constraints": _risk_constraints(result),
        "signals": rows,
        # Backward-readable aliases during the protocol migration window.
        "strategy_name": result.spec.name,
        "spec_version": result.spec.schema_version,
        "data_end": result.end_date,
    }
    validate_strategy_signal_v1(payload)
    if output_dir:
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        base = _safe_name(result.spec.name)
        json_path = out_dir / f"{base}.signals.json"
        csv_path = out_dir / f"{base}.signals.csv"
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        _write_csv(csv_path, rows)
        payload["json_path"] = str(json_path)
        payload["csv_path"] = str(csv_path)
    return payload


def _export_rows(result: StrategyBacktestResult) -> list[dict]:
    if result.target_weights.empty:
        return []
    latest_date = pd.to_datetime(result.target_weights["trade_date"]).max()
    latest = result.target_weights[pd.to_datetime(result.target_weights["trade_date"]) == latest_date].copy()
    risk_by_stock = _risk_reasons(result.risk_logs, latest_date)
    rows = []
    for rank, row in enumerate(latest.sort_values("target_weight", ascending=False).itertuples(index=False), start=1):
        stock_code = str(row.stock_code)
        rows.append({
            "trade_date": pd.Timestamp(row.trade_date).strftime("%Y-%m-%d"),
            "stock_code": stock_code,
            "target_weight": round(float(row.target_weight), 8),
            "rank": rank,
            "constraint_reasons": risk_by_stock.get(stock_code, []),
            "notice": NON_LIVE_NOTICE,
        })
    return rows


def _validation_summary(result: StrategyBacktestResult, *, data_snapshot_id: str | None) -> dict:
    oos_result = result.oos_result or {}
    return {
        "oos_enabled": bool(oos_result),
        "direction_policy": result.direction_policy or oos_result.get("direction_policy"),
        "train_period": _period(oos_result, "train"),
        "validation_period": _period(oos_result, "valid"),
        "test_period": _period(oos_result, "test"),
        "promotion_gate_passed": False,
        "data_snapshot_id": data_snapshot_id,
    }


def _risk_constraints(result: StrategyBacktestResult) -> dict:
    risk_rules = result.spec.risk_rules
    return {
        "max_single_name_weight": risk_rules.max_asset_weight,
        "max_turnover": risk_rules.max_turnover,
        "long_only": not risk_rules.allow_short,
        "allow_short": risk_rules.allow_short,
    }


def _period(oos_result: dict, stage: str) -> list | None:
    payload = oos_result.get(stage)
    if not isinstance(payload, dict):
        return None
    period = payload.get("period")
    return period if isinstance(period, list) else None


def _risk_reasons(risk_logs: list[dict], trade_date: pd.Timestamp) -> dict[str, list[str]]:
    date_str = trade_date.strftime("%Y-%m-%d")
    reasons: dict[str, list[str]] = {}
    for log in risk_logs:
        if log.get("trade_date") != date_str:
            continue
        stock_code = log.get("stock_code")
        if not stock_code:
            continue
        reasons.setdefault(str(stock_code), []).append(str(log.get("code", "RISK_RULE_APPLIED")))
    return reasons


def _write_csv(path: Path, rows: list[dict]) -> None:
    fieldnames = ["trade_date", "stock_code", "target_weight", "rank", "constraint_reasons", "notice"]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, "constraint_reasons": ";".join(row["constraint_reasons"])})


def _safe_name(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in name).strip("_") or "strategy"


def _assert_no_forbidden_keys(value) -> None:
    assert_no_forbidden_execution_fields(value)
