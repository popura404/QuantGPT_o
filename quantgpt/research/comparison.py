"""Paired, selection-only comparisons of immutable research strategy runs."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import cast

import numpy as np
import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from quantgpt.research.evaluations import get_evaluation, read_artifact, write_artifact
from quantgpt.research.hypotheses import lock_research_registration
from quantgpt.research.projects import require_project_member
from quantgpt.research.strategies import get_strategy_run
from quantgpt.research.validation_runner import _read_returns
from quantgpt.statistics.multiple_testing import bootstrap_mean_ci


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _check_augmented(baseline: dict, augmented: dict) -> list[str]:
    if baseline["scope"] != "selection" or augmented["scope"] != "selection":
        raise ValueError("COMPARISON_REQUIRES_SELECTION_RUNS")
    if baseline["config"] != augmented["config"]:
        raise ValueError("COMPARISON_CONFIG_MISMATCH: windows, snapshots and simulation must match")
    specs = [run["spec"] for run in (baseline, augmented)]
    for name in ("signal_rules", "portfolio_rule", "cost_model", "risk_rules", "simulation_config", "validation"):
        if specs[0][name] != specs[1][name]:
            raise ValueError(f"COMPARISON_STRATEGY_RULE_MISMATCH: {name}")
    components = []
    for spec in specs:
        weights = {ref["evaluation_id"]: factor["weight"]
                   for ref, factor in zip(spec["factor_evaluations"], spec["factors"], strict=True)}
        if len(weights) != len(spec["factors"]):
            raise ValueError("DUPLICATE_COMPARISON_COMPONENT")
        components.append(weights)
    before, after = components
    if not before.keys() < after.keys():
        raise ValueError("AUGMENTED_STRATEGY_MUST_RETAIN_AND_ADD_COMPONENTS")
    ratios = [after[key] / weight for key, weight in before.items()]
    if not np.allclose(ratios, ratios[0], atol=0, rtol=1e-8):
        raise ValueError("BASELINE_RELATIVE_FACTOR_WEIGHTS_CHANGED")
    return sorted(after.keys() - before.keys())


async def _load_signals(session: AsyncSession, actor_id: uuid.UUID, project_id: uuid.UUID, run: dict) -> list:
    ref = run["signal_ref"]
    artifact = await read_artifact(session, actor_id, project_id, uuid.UUID(ref["artifact_id"]))
    if (artifact["kind"] != "signal" or artifact["content_sha256"] != ref["content_sha256"]
            or artifact["metadata"]["evaluation_hash"] != run["evaluation_hash"]):
        raise ValueError("SIGNAL_LINEAGE_MISMATCH")
    return artifact["payload"]


def _signal_series(records: list) -> pd.Series:
    frame = pd.DataFrame(records)
    if frame.empty:
        return pd.Series(dtype=float, index=pd.MultiIndex.from_arrays([[], []], names=["trade_date", "stock_code"]))
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    if (frame[["trade_date", "stock_code"]].isna().to_numpy().any()
            or frame.duplicated(["trade_date", "stock_code"]).any() or not np.isfinite(frame["factor_value"]).all()):
        raise ValueError("INVALID_STRATEGY_SIGNALS")
    return cast(pd.Series, frame.set_index(["trade_date", "stock_code"])["factor_value"])


def _paired_metrics(baseline: pd.Series, augmented: pd.Series, signals: tuple[list, list], horizon: int) -> dict:
    if baseline.empty or not baseline.index.equals(augmented.index):
        raise ValueError("PAIRED_RETURN_SESSIONS_REQUIRED")
    delta = augmented - baseline
    block = max(horizon, int(np.ceil(len(delta) ** (1 / 3))))
    interval = bootstrap_mean_ci(delta.tolist(), seed=0, block_length=block) if len(delta) >= max(20, 4 * block) else None
    series = [_signal_series(records).rename(name) for name, records in zip(("baseline", "augmented"), signals, strict=True)]
    aligned = pd.concat(series, axis=1)
    correlations = []
    for _, cross_section in aligned.groupby(level="trade_date"):
        paired = cross_section.dropna()
        if len(paired) >= 3 and paired.nunique().min() > 1:
            first, second = cast(pd.Series, paired["baseline"]), cast(pd.Series, paired["augmented"])
            correlations.append(float(first.corr(second, method="spearman")))
    before, after = (float((1 + item).prod() - 1) for item in (baseline, augmented))
    return {"sessions": len(delta), "start_session": cast(pd.Timestamp, baseline.index[0]).date().isoformat(),
            "end_session": cast(pd.Timestamp, baseline.index[-1]).date().isoformat(),
            "net_total_return": {"baseline": before, "augmented": after, "difference": after - before},
            "paired_mean_daily_net_return_difference": float(delta.mean()),
            "paired_mean_interval": interval, "interval_scope": "descriptive_selection_unadjusted",
            "signal_similarity": {"method": "mean_daily_cross_sectional_spearman",
                                  "value": float(np.mean(correlations)) if correlations else None,
                                  "comparable_sessions": len(correlations), "security_sessions_common": len(aligned.dropna()),
                                  "security_sessions_union": len(aligned)}}


async def compare_research_strategies(session: AsyncSession, actor_id: uuid.UUID, project_id: uuid.UUID,
                                     round_id: uuid.UUID, augmented_run_id: uuid.UUID) -> dict:
    """Resolve the preregistered baseline and proposals from the authorized research round."""
    from quantgpt.research.rounds import get_round, round_evaluation_ids

    await require_project_member(session, actor_id, project_id, write=True)
    research_round = await get_round(session, actor_id, project_id, round_id)
    if research_round.baseline_run_id is None:
        raise ValueError("ROUND_HAS_NO_FROZEN_STRATEGY_BASELINE")
    baseline_row = await get_strategy_run(session, actor_id, project_id, research_round.baseline_run_id)
    augmented_row = await get_strategy_run(session, actor_id, project_id, augmented_run_id)
    baseline, augmented = cast(dict, baseline_row.result), cast(dict, augmented_row.result)
    added = _check_augmented(baseline, augmented)
    before = await get_evaluation(session, actor_id, project_id, baseline["evaluation_id"])
    after = await get_evaluation(session, actor_id, project_id, augmented["evaluation_id"])
    frozen_at = _as_utc(research_round.created_at)
    if (_as_utc(cast(datetime, baseline_row.created_at)) > frozen_at
            or _as_utc(cast(datetime, after.created_at)) <= frozen_at):
        raise ValueError("BASELINE_MUST_BE_FROZEN_BEFORE_AUGMENTED_EVALUATION")
    if baseline["config"] != research_round.selection_config:
        raise ValueError("ROUND_SELECTION_CONFIG_MISMATCH")
    if not set(added).issubset(await round_evaluation_ids(session, round_id)):
        raise ValueError("AUGMENTED_COMPONENT_OUTSIDE_ROUND_PROPOSALS")
    before_returns, before_ref = await _read_returns(session, actor_id, project_id, before)
    after_returns, after_ref = await _read_returns(session, actor_id, project_id, after)
    signals = (await _load_signals(session, actor_id, project_id, baseline),
               await _load_signals(session, actor_id, project_id, augmented))
    metrics = await asyncio.to_thread(_paired_metrics, before_returns, after_returns, signals,
                                      baseline["config"]["window"]["label_horizon_sessions"])
    turnover = [float(run["metrics"]["turnover"]) for run in (baseline, augmented)]
    report = {"schema_version": "strategy_incremental_evidence/v1", "scope": "selection",
              "project_id": str(project_id), "round_id": str(round_id),
              "baseline_run_id": baseline["strategy_run_id"], "augmented_run_id": str(augmented_run_id),
              "baseline_hash": baseline["strategy_hash"], "augmented_hash": augmented["strategy_hash"],
              "added_factor_evaluations": added, "source_artifacts": [before_ref, after_ref, baseline["signal_ref"],
                                                                       augmented["signal_ref"]],
              **metrics, "mean_daily_turnover": {"baseline": turnover[0], "augmented": turnover[1],
                  "difference": turnover[1] - turnover[0], "convention": "half_absolute_trade_notional_over_prior_nav"},
              "research_decision": "descriptive", "external_data_acceptance": "not_run"}
    await lock_research_registration(session, actor_id, project_id)
    reference = await write_artifact(session, after, "strategy_incremental_evidence", report)
    await session.commit()
    return {**report, "artifact_ref": reference}
