"""Explicit synthetic offline fixture for the complete research workflow."""

from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from quantgpt.data_snapshots import freeze_market_frame
from quantgpt.research.contracts import (
    US_RESEARCH_FIELDS,
    DataInputRef,
    EvaluationConfigV1,
    EvaluationWindow,
    FactorDefinitionV1,
    ResearchScope,
    SimulationConfigV1,
    content_hash,
)
from quantgpt.research.runtime import local_engine_identity


def prepare_offline_demo(root: str | Path) -> dict:
    rng = np.random.default_rng(20261005)
    sessions = pd.bdate_range("2024-01-02", periods=180)
    rows = []
    for index in range(20):
        prices = (20 + index) * np.cumprod(1 + rng.normal(0.0002, 0.009, len(sessions)))
        for day, close in zip(sessions, prices, strict=True):
            rows.append({"trade_date": day, "stock_code": f"DG.{index + 1:03d}", "open": float(close * .999),
                         "close": float(close), "high": float(close * 1.01), "low": float(close * .99),
                         "volume": 100_000.0, "amount": float(close * 100_000)})
    panel = pd.DataFrame(rows)
    panel.attrs.update({"calendar_sessions": [day.isoformat() for day in sessions], "adjustment": "raw",
                        "research_only": True, "synthetic": True, "corporate_actions": []})
    manifest = freeze_market_frame(panel, root, vendor="synthetic_offline_fixture",
        source_metadata={"synthetic": True, "adjustment": "raw", "license": "repository-generated",
                         "real_market_validation": False})
    simulation = SimulationConfigV1(rebalance_anchor_session=date.fromisoformat(str(sessions[0])[:10]),
                                    rebalance_every_sessions=5, fees_bps=10)
    config = EvaluationConfigV1(backend="local", direction="higher_is_better",
        scope=ResearchScope(market="demo_global_equity", currency="USD", universe_id="demo_large",
            universe_mode="fixed_cohort", universe_version="offline_fixture/v1", calendar_id="synthetic_weekdays",
            calendar_version="1", timezone="America/New_York", benchmark="demo_world"),
        window=EvaluationWindow(start_session=date.fromisoformat(str(sessions[40])[:10]),
            end_session=date.fromisoformat(str(sessions[-1])[:10]),
            warmup_observations=40, split_id="offline_demo_selection",
            split_hash=content_hash("quantgpt.demo/split/v1", [str(sessions[40]), str(sessions[-1])]),
            label_horizon_sessions=5, purge_sessions=5),
        simulation_config=simulation,
        data_inputs=(DataInputRef(manifest_id=manifest["snapshot_id"],
            content_sha256=manifest["content_hash"].removeprefix("sha256:"), version_status="verified",
            source="synthetic_offline_fixture", feed="generated/v1", adjustment="raw",
            asof=datetime(2025, 1, 1, tzinfo=timezone.utc)),),
        engine=local_engine_identity())
    definition = FactorDefinitionV1(expression="rank(close / ts_mean(close, 20))",
        fields=(next(field for field in US_RESEARCH_FIELDS if field.name == "close"),))
    template = {"schema_version": "strategy_spec/v2", "name": "Offline contract demonstration",
        "asset_class": "equity", "market": config.scope.market, "frequency": "daily", "universe": "demo_large",
        "factors": [{"id": "momentum_20", "expression": definition.expression,
                     "direction": config.direction, "weight": 1.0}], "factor_evaluations": [],
        "semantics_version": definition.semantics_version, "simulation_config": simulation.model_dump(mode="json"),
        "signal_rules": {"type": "rank_threshold", "top_n": 5},
        "portfolio_rule": {"weighting": "equal_weight", "rebalance_period": 5},
        "risk_rules": {"allow_short": False, "max_asset_weight": .2, "max_turnover": 2},
        "cost_model": {"type": "fixed_bps", "bps": 10},
        "validation": {"min_history_days": 30}, "outputs": {"report": True, "signal_export": False}}
    return {"synthetic": True, "notice": "Offline contract fixture; not real US market evidence",
            "field_catalog": [field.model_dump(mode="json") for field in US_RESEARCH_FIELDS],
            "config": config.model_dump(mode="json"), "definitions": [definition.model_dump(mode="json")],
            "strategy_template": template}
