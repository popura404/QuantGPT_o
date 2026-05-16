"""Candidate strategy signal export helpers."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pandas as pd

from .result import StrategyBacktestResult

NON_LIVE_NOTICE = "Candidate rebalance signal only; not an order or automated trading instruction."
FORBIDDEN_EXPORT_KEYS = {"broker", "account", "order", "api_key", "execution"}


def export_strategy_candidate(result: StrategyBacktestResult, output_dir: str | None = None) -> dict:
    rows = _export_rows(result)
    payload = {
        "strategy_name": result.spec.name,
        "spec_version": result.spec.schema_version,
        "market": result.spec.market,
        "data_end": result.end_date,
        "notice": NON_LIVE_NOTICE,
        "signals": rows,
    }
    _assert_no_forbidden_keys(payload)
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
    for row in latest.sort_values("target_weight", ascending=False).itertuples(index=False):
        stock_code = str(row.stock_code)
        rows.append({
            "trade_date": pd.Timestamp(row.trade_date).strftime("%Y-%m-%d"),
            "stock_code": stock_code,
            "target_weight": round(float(row.target_weight), 8),
            "action_hint": "buy" if float(row.target_weight) > 0 else "hold",
            "constraint_reasons": risk_by_stock.get(stock_code, []),
            "notice": NON_LIVE_NOTICE,
        })
    return rows


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
    fieldnames = ["trade_date", "stock_code", "target_weight", "action_hint", "constraint_reasons", "notice"]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, "constraint_reasons": ";".join(row["constraint_reasons"])})


def _safe_name(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in name).strip("_") or "strategy"


def _assert_no_forbidden_keys(value) -> None:
    if isinstance(value, dict):
        forbidden = FORBIDDEN_EXPORT_KEYS & set(value)
        if forbidden:
            raise ValueError(f"Forbidden signal export keys: {sorted(forbidden)}")
        for item in value.values():
            _assert_no_forbidden_keys(item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_forbidden_keys(item)
