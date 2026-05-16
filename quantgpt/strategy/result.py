"""Strategy backtest result contracts."""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .spec import StrategySpecV0


@dataclass(slots=True)
class StrategyBacktestResult:
    spec: StrategySpecV0
    start_date: str
    end_date: str
    benchmark: str
    strategy_returns: pd.Series
    target_weights: pd.DataFrame
    cash_weights: pd.DataFrame
    turnover_by_rebalance: pd.DataFrame
    cost_by_rebalance: pd.DataFrame
    risk_logs: list[dict]
    latest_holdings: list[dict]
    metrics: dict
    validation_issues: list[dict] = field(default_factory=list)
    diagnostics: dict = field(default_factory=dict)
    factor_frame: pd.DataFrame | None = None

    def to_summary(self) -> dict:
        return {
            "spec_version": self.spec.schema_version,
            "strategy_name": self.spec.name,
            "market": self.spec.market,
            "universe": self.spec.universe,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "benchmark": self.benchmark,
            "data_end": self.end_date,
            "metrics": _json_safe(self.metrics),
            "latest_holdings": _json_safe(self.latest_holdings),
            "risk_logs": _json_safe(self.risk_logs),
            "validation_issues": _json_safe(self.validation_issues),
            "diagnostics": _json_safe(self.diagnostics),
            "non_live_trading_notice": "Candidate strategy only; not an automated trading instruction.",
        }


def _json_safe(value):
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            return value
    return value
