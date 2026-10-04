"""Immutable strategy runs with server-verified component lineage and exports."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import cast

import pandas as pd
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from quantgpt.experiment_ledger import record_experiment
from quantgpt.models import Experiment, Strategy, StrategyRun
from quantgpt.research.contracts import EvaluationConfigV1, FactorDefinitionV1, strategy_hash
from quantgpt.research.evaluations import (
    assert_publication_attempt,
    authorize_data_inputs,
    get_evaluation,
    guard_evaluation_window,
    load_evaluation_panel,
    read_artifact,
    serialize_frame,
    verify_frozen_config,
    write_artifact,
)
from quantgpt.research.projects import ProjectAccessError, require_project_member
from quantgpt.research.runtime import normalize_local_config
from quantgpt.strategy.backtest import StrategyBacktestRequest, run_strategy_backtest
from quantgpt.strategy.signals import build_rank_threshold_signals
from quantgpt.strategy.spec import StrategySpecV2


async def check_factor_lineage(session: AsyncSession, actor_id: uuid.UUID, project_id: uuid.UUID,
                               spec: StrategySpecV2, config: EvaluationConfigV1) -> None:
    if (spec.market, spec.universe) != (config.scope.market, config.scope.universe_id):
        raise ValueError("STRATEGY_SCOPE_MISMATCH")
    if spec.simulation_config != config.simulation_config:
        raise ValueError("STRATEGY_SIMULATION_MISMATCH")
    if spec.validation.oos is not None and spec.validation.oos.enabled:
        raise ValueError("V2 uses the registered evaluation window; nested legacy OOS splits are not supported")
    for factor, ref in zip(spec.factors, spec.factor_evaluations, strict=True):
        if ref.project_id != str(project_id):
            raise ProjectAccessError("Component not found or inaccessible")
        row = await get_evaluation(session, actor_id, project_id, ref.evaluation_id)
        component_config = verify_frozen_config(row)
        definition = FactorDefinitionV1.model_validate((row.evaluation_config or {})["definition"])
        if ref.evaluation_hash != row.evaluation_hash or ref.definition_hash != row.definition_hash:
            raise ValueError("FACTOR_REFERENCE_INTEGRITY_MISMATCH")
        if component_config.backend != "local" or ref.scope != component_config.window.phase:
            raise ValueError("FACTOR_EVIDENCE_SCOPE_MISMATCH")
        if component_config.scope != config.scope or component_config.direction != factor.direction:
            raise ValueError("FACTOR_SCOPE_OR_DIRECTION_MISMATCH")
        if (component_config.data_inputs != config.data_inputs or component_config.engine != config.engine
                or component_config.neutralization != config.neutralization
                or component_config.simulation_config != config.simulation_config
                or definition.semantics_version != spec.semantics_version):
            raise ValueError("FACTOR_DATA_OR_VERSION_MISMATCH: recompute components under this frozen configuration")
        if component_config.window.phase != "selection":
            raise ValueError("STRATEGY_REQUIRES_SELECTION_LINEAGE: final evidence cannot enter candidate selection")
        if config.window.phase == "selection" and component_config.window != config.window:
            raise ValueError("FACTOR_WINDOW_MISMATCH")
        if config.window.phase == "final" and (
            component_config.window.split_hash != config.window.split_hash
            or component_config.window.end_session >= config.window.start_session
        ):
            raise ValueError("FACTOR_SPLIT_MISMATCH")
        if definition.expression.strip() != factor.expression.strip():
            raise ValueError("FACTOR_EXPRESSION_MISMATCH")
        if not (row.result_summary or {}).get("performance_observed"):
            raise ValueError("FACTOR_EVALUATION_REQUIRED")


async def get_strategy_run(session: AsyncSession, actor_id: uuid.UUID, project_id: uuid.UUID,
                            run_id: uuid.UUID) -> StrategyRun:
    await require_project_member(session, actor_id, project_id)
    stored = (await session.execute(select(StrategyRun, Strategy).join(Strategy).where(
        StrategyRun.id == run_id, Strategy.project_id == project_id))).one_or_none()
    if stored is None:
        raise ProjectAccessError("Strategy run not found or inaccessible")
    run, strategy = stored
    row = (await session.scalars(select(Experiment).where(
        Experiment.project_id == project_id, Experiment.strategy_id == strategy.id,
        Experiment.strategy_run_id == run.id,
    ))).one_or_none()
    payload = run.result
    if row is None or not isinstance(payload, dict) or cast(str, strategy.schema_version) != "strategy_spec/v2":
        raise ValueError("STRATEGY_RUN_ORIGIN_REQUIRED")
    frozen = row.evaluation_config or {}
    if "strategy" not in frozen or "config" not in frozen:
        raise ValueError("STRATEGY_RUN_ORIGIN_REQUIRED")
    config = verify_frozen_config(row)
    spec = StrategySpecV2.model_validate(strategy.spec).model_dump(mode="json")
    summary = row.result_summary or {}
    evaluation_id = cast(str, row.experiment_id)
    if (frozen.get("strategy") != spec or payload.get("spec") != spec
            or payload.get("config") != config.model_dump(mode="json")
            or payload.get("schema_version") != "strategy_run/v2"
            or payload.get("strategy_run_id") != str(run_id) or payload.get("project_id") != str(project_id)
            or payload.get("evaluation_id") != evaluation_id
            or payload.get("strategy_hash") != row.evaluation_hash
            or payload.get("evaluation_hash") != row.evaluation_hash
            or payload.get("scope") != config.window.phase or row.backend != "local"
            or cast(uuid.UUID, strategy.id) != run_id
            or evaluation_id != "se_" + run_id.hex
            or run_id != uuid.uuid5(project_id, "strategy:" + str(row.evaluation_hash))
            or not {"metrics", "diagnostics", "signal_ref", "artifacts"}.issubset(payload)
            or any(key not in summary or summary[key] != value for key, value in payload.items())):
        raise ValueError("STRATEGY_RUN_INTEGRITY_MISMATCH")
    return run


async def run_strategy_evaluation(session: AsyncSession, actor_id: uuid.UUID, project_id: uuid.UUID,
                                   spec: StrategySpecV2, config: EvaluationConfigV1, *,
                                   snapshot_root: str | Path, cancel_check=None,
                                   task_context: dict | None = None) -> dict:
    await require_project_member(session, actor_id, project_id, write=True)
    config = normalize_local_config(config)
    await authorize_data_inputs(session, project_id, config)
    await check_factor_lineage(session, actor_id, project_id, spec, config)
    identity = strategy_hash(spec.model_dump(mode="json"), config, spec.factor_evaluations)
    run_id = uuid.uuid5(project_id, "strategy:" + identity)
    existing = await session.get(StrategyRun, run_id)
    if existing is not None:
        existing = await get_strategy_run(session, actor_id, project_id, run_id)
        return cast(dict, existing.result)
    evaluation_id = "se_" + run_id.hex
    row = (await session.scalars(select(Experiment).where(Experiment.experiment_id == evaluation_id))).one_or_none()
    if row is None:
        row = await record_experiment(session, expression="; ".join(f.expression for f in spec.factors),
            user_id=actor_id, experiment_id=evaluation_id, params={"market": spec.market, "universe": spec.universe,
            "validation_stage": config.window.phase, "research_mode": "research_only"})
        row.project_id = project_id
        row.backend = "local"
        row.evaluation_hash = identity
        row.evaluation_config = {"strategy": spec.model_dump(mode="json"), "config": config.model_dump(mode="json")}
        row.evidence_status = "research_only"
    await guard_evaluation_window(session, actor_id, row, config)
    await session.commit()
    panel = load_evaluation_panel(config, snapshot_root)
    panel = cast(pd.DataFrame, panel[pd.to_datetime(panel["trade_date"]) <= pd.Timestamp(config.window.end_session)].copy())
    panel.attrs["frozen_research_input"] = True
    numeric_spec = spec.calculation_spec()
    request = StrategyBacktestRequest(spec=numeric_spec, start_date=config.window.start_session.isoformat(),
        end_date=config.window.end_session.isoformat(), benchmark=config.scope.benchmark,
        simulation_config=spec.simulation_config,
        neutralize_industry="industry" in config.neutralization, neutralize_cap="market_cap" in config.neutralization,
        validation_stage="final" if config.window.phase == "final" else "selection")
    result = await asyncio.to_thread(run_strategy_backtest, request, panel, final_authorized=True)
    if cancel_check:
        cancel_check()
    await assert_publication_attempt(session, task_context)
    await require_project_member(session, actor_id, project_id, write=True)
    if result.factor_frame is None:
        raise ValueError("STRATEGY_SIGNAL_ARTIFACT_MISSING")
    signal_frame = build_rank_threshold_signals(result.factor_frame, numeric_spec)
    signal_frame = cast(pd.DataFrame, signal_frame[pd.to_datetime(signal_frame["trade_date"]) >= pd.Timestamp(config.window.start_session)])
    signal = await write_artifact(session, row, "signal", serialize_frame(signal_frame))
    returns = await write_artifact(session, row, "returns", serialize_frame(
        result.strategy_returns.rename("return").rename_axis("session").reset_index()))
    weights = await write_artifact(session, row, "target_weights", serialize_frame(result.target_weights))
    report = await write_artifact(session, row, "report", result.to_summary())
    payload = {"schema_version": "strategy_run/v2", "strategy_run_id": str(run_id), "project_id": str(project_id),
        "strategy_hash": identity, "evaluation_hash": identity, "evaluation_id": evaluation_id,
        "scope": config.window.phase, "spec": spec.model_dump(mode="json"), "config": config.model_dump(mode="json"),
        "metrics": result.to_summary()["metrics"], "diagnostics": result.to_summary()["diagnostics"],
        "signal_ref": signal, "artifacts": [signal, returns, weights, report], "evidence_status": "research_only",
        "research_decision": "not_evaluated", "blockers": ["VALIDATION_PROFILE_INCOMPLETE"]}
    row.result_summary = {**payload, "performance_observed": True}
    row.status = "validated_oos" if config.window.phase == "final" else "backtested_train"
    strategy = Strategy(id=run_id, user_id=actor_id, project_id=project_id, name=spec.name,
        schema_version=spec.schema_version, market=spec.market, universe=spec.universe, spec=spec.model_dump(mode="json"))
    session.add(strategy)
    await session.flush()
    session.add(StrategyRun(id=run_id, strategy_id=run_id, user_id=actor_id, result=payload))
    await session.flush()
    await session.execute(update(Experiment).where(Experiment.id == row.id).values(
        strategy_id=run_id, strategy_run_id=run_id, strategy_spec_version=spec.schema_version))
    await session.commit()
    return payload


async def export_strategy_run(session: AsyncSession, actor_id: uuid.UUID, project_id: uuid.UUID,
                                run_id: uuid.UUID) -> dict:
    from quantgpt.research.validation import evaluate_profile

    run = await get_strategy_run(session, actor_id, project_id, run_id)
    payload = cast(dict, run.result)
    row = await get_evaluation(session, actor_id, project_id, payload["evaluation_id"])
    config = verify_frozen_config(row)
    if payload["strategy_hash"] != row.evaluation_hash:
        raise ValueError("STRATEGY_RUN_INTEGRITY_MISMATCH")
    decision = await evaluate_profile(session, actor_id, project_id, str(row.experiment_id), profile="local_strategy")
    if not decision["allowed"]:
        return {"exported": False, "validation": decision}
    signal = await read_artifact(session, actor_id, project_id, uuid.UUID(payload["signal_ref"]["artifact_id"]))
    if signal["metadata"]["evaluation_hash"] != row.evaluation_hash or signal["kind"] != "signal":
        raise ValueError("SIGNAL_LINEAGE_MISMATCH")
    return {"exported": True, "schema_version": "strategy_signal.v2", "strategy_run_id": str(run_id),
            "strategy_hash": row.evaluation_hash, "scope": config.window.phase,
            "signal_ref": payload["signal_ref"], "signals": signal["payload"], "validation": decision,
            "non_live_trading_notice": "Research candidate signals only"}
