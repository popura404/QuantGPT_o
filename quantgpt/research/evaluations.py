"""Project-scoped immutable evaluations and server-owned evidence artifacts.

Legacy factor_hash is preserved on Experiment. New content identities do not
accept caller-supplied success booleans as validation or export authorization.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import cast

from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from quantgpt.data_snapshots import load_frozen_market_frame
from quantgpt.experiment_ledger import record_experiment
from quantgpt.models import (
    Experiment,
    ExperimentArtifact,
    FinalWindowExposure,
    ResearchAuditEvent,
    ResearchProject,
    Task,
)
from quantgpt.research.contracts import (
    EvaluationConfigV1,
    FactorDefinitionV1,
    SimulationConfigV1,
    canonical_json,
    content_hash,
    definition_hash,
    evaluation_hash,
)
from quantgpt.research.projects import ProjectAccessError, require_project_member


async def get_evaluation(session: AsyncSession, actor_id: uuid.UUID, project_id: uuid.UUID,
                          evaluation_id: str) -> Experiment:
    await require_project_member(session, actor_id, project_id)
    row = (await session.scalars(select(Experiment).where(Experiment.experiment_id == evaluation_id,
                                                         Experiment.project_id == project_id))).one_or_none()
    if row is None:
        raise ProjectAccessError("Evaluation not found or inaccessible")
    return row


async def authorize_data_inputs(session: AsyncSession, project_id: uuid.UUID, config: EvaluationConfigV1) -> None:
    """A manifest hash is an identity, never authorization to another project's data."""
    available = set((await session.scalars(select(ResearchAuditEvent.payload["snapshot_id"].as_string()).where(
        ResearchAuditEvent.project_id == project_id, ResearchAuditEvent.action == "snapshot_registered"))).all())
    if any(item.manifest_id not in available for item in config.data_inputs):
        raise ProjectAccessError("Snapshot not registered in this project")


async def assert_publication_attempt(session: AsyncSession, task_context: dict | None) -> None:
    if task_context is None:
        return
    from datetime import datetime, timezone

    from quantgpt.task_store import CancelledException

    valid = await session.execute(update(Task).where(Task.id == task_context["task_id"],
        Task.attempt_id == task_context["attempt_id"], Task.status == "running",
        Task.cancel_requested.is_(False), Task.lease_expires_at > datetime.now(timezone.utc))
        .values(revision=Task.revision).execution_options(synchronize_session=False))
    # Retain this row lock through artifact publication/commit. A concurrent
    # cancel either wins first or observes the fully committed publication.
    if cast(CursorResult, valid).rowcount != 1:
        raise CancelledException("TASK_ATTEMPT_NO_LONGER_AUTHORIZED")


async def register_evaluation(session: AsyncSession, actor_id: uuid.UUID, project_id: uuid.UUID,
                              definition: FactorDefinitionV1, config: EvaluationConfigV1) -> tuple[Experiment, bool]:
    await require_project_member(session, actor_id, project_id, write=True)
    identity = evaluation_hash(definition, config)
    evaluation_id = "ev_" + uuid.uuid5(project_id, identity).hex
    existing = (await session.scalars(select(Experiment).where(Experiment.experiment_id == evaluation_id))).one_or_none()
    if existing is not None:
        return existing, False
    from quantgpt.research.hypotheses import lock_research_registration

    await lock_research_registration(session, actor_id, project_id)
    row = await record_experiment(session, expression=definition.expression, user_id=actor_id,
                                  experiment_id=evaluation_id, params={"market": config.scope.market,
                                  "universe": config.scope.universe_id, "validation_stage": config.window.phase,
                                  "direction_policy": "manual", "research_mode": "research_only"})
    row.project_id = project_id
    row.definition_hash = definition_hash(definition)
    row.evaluation_hash = identity
    row.evaluation_config = {"definition": definition.model_dump(mode="json"), "config": config.model_dump(mode="json")}
    row.backend = config.backend
    row.evidence_status = "platform_only" if config.backend == "wq" else "research_only"
    await session.flush()
    return row, True


async def write_artifact(session: AsyncSession, evaluation: Experiment, kind: str, payload: dict | list) -> dict:
    """Internal runner API; no public endpoint permits writing validation proofs."""
    digest = content_hash(f"quantgpt.artifact/{kind}/v1", payload)
    artifact_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{evaluation.experiment_id}:{kind}:{digest}")
    existing = await session.get(ExperimentArtifact, artifact_id)
    if existing is None:
        session.add(ExperimentArtifact(id=artifact_id, experiment_id=evaluation.experiment_id, artifact_type=kind,
                                       uri=f"artifact:{artifact_id}", content_hash=digest, payload=payload,
                                       artifact_metadata={"project_id": str(evaluation.project_id),
                                                          "evaluation_hash": evaluation.evaluation_hash,
                                                          "schema_version": f"{kind}/v1"}))
        await session.flush()
    return {"artifact_id": str(artifact_id), "kind": kind, "content_sha256": digest,
            "project_id": str(evaluation.project_id), "evaluation_id": evaluation.experiment_id}


async def read_artifact(session: AsyncSession, actor_id: uuid.UUID, project_id: uuid.UUID, artifact_id: uuid.UUID) -> dict:
    await require_project_member(session, actor_id, project_id)
    row = (await session.scalars(select(ExperimentArtifact).join(Experiment).where(
        ExperimentArtifact.id == artifact_id, Experiment.project_id == project_id))).one_or_none()
    if row is None:
        raise ProjectAccessError("Artifact not found or inaccessible")
    if row.payload is None or str(row.content_hash) != content_hash(f"quantgpt.artifact/{row.artifact_type}/v1", row.payload):
        raise ValueError("Artifact content integrity check failed")
    return {"artifact_id": str(row.id), "kind": row.artifact_type, "payload": row.payload,
            "content_sha256": row.content_hash, "metadata": row.artifact_metadata}


async def register_holdout(session: AsyncSession, actor_id: uuid.UUID, project_id: uuid.UUID,
                            *, start: str, end: str) -> dict:
    from datetime import date

    await require_project_member(session, actor_id, project_id, write=True)
    if date.fromisoformat(start) >= date.fromisoformat(end):
        raise ValueError("Holdout start must precede end")
    from quantgpt.research.hypotheses import lock_research_registration

    await lock_research_registration(session, actor_id, project_id)
    project = await session.get(ResearchProject, project_id, with_for_update=True)
    if project is None:
        raise ProjectAccessError("Project not found")
    requested = {"start": start, "end": end}
    previous = project.config.get("holdout")
    if isinstance(previous, dict) and previous == requested:
        return previous
    trials = await session.scalar(select(func.count()).select_from(Experiment).where(Experiment.project_id == project_id))
    if previous or trials:
        raise ValueError("Holdout must be registered before research starts and cannot be overwritten")
    project.config = {**project.config, "holdout": requested}
    await session.flush()
    return requested


async def guard_evaluation_window(session: AsyncSession, actor_id: uuid.UUID, evaluation: Experiment,
                                   config: EvaluationConfigV1) -> None:
    """Freeze/expose at project level; changing ID, lineage or snapshot cannot reset it."""
    if evaluation.project_id is None:
        raise ProjectAccessError("Legacy evaluations cannot expose a research project holdout")
    await require_project_member(session, actor_id, evaluation.project_id, write=True)
    project = await session.get(ResearchProject, evaluation.project_id)
    if project is None:
        raise ProjectAccessError("Project not found")
    holdout = project.config.get("holdout")
    start, end = config.window.start_session.isoformat(), config.window.end_session.isoformat()
    if config.window.phase == "selection":
        # A later selection window would still read the holdout as lookback
        # history, so checking only score-window overlap is insufficient.
        if holdout and end >= holdout["start"]:
            raise ValueError("FINAL_WINDOW_WITHHELD: selection and its feature history must precede the holdout")
        return
    if config.window.phase == "platform":
        return
    if not holdout or start != holdout["start"] or end != holdout["end"]:
        raise ValueError("FINAL_WINDOW_NOT_PREREGISTERED")
    # Compare-and-swap serializes competing reservations on SQLite as well as PG.
    revision = project.revision
    result = await session.execute(update(ResearchProject).where(ResearchProject.id == project.id,
                                                                 ResearchProject.revision == revision)
                                   .values(revision=revision + 1))
    if cast(CursorResult, result).rowcount != 1:
        raise ValueError("FINAL_RESERVATION_CONFLICT: retry reading current project state")
    key = content_hash("quantgpt.holdout/v1", holdout)
    exposure = await session.get(FinalWindowExposure, (project.id, "project", key))
    if exposure is not None:
        if exposure.evaluation_hash != evaluation.evaluation_hash:
            raise ValueError("FINAL_WINDOW_ALREADY_EXPOSED: another candidate requires an independent window")
        return
    session.add(FinalWindowExposure(project_id=project.id, family_key="project", window_key=key,
                                    evaluation_hash=evaluation.evaluation_hash, evaluation_id=evaluation.experiment_id,
                                    frozen_config=config.model_dump(mode="json"), actor_id=actor_id))
    await session.flush()


def load_evaluation_panel(config: EvaluationConfigV1, snapshot_root: str | Path):
    if config.backend != "local":
        raise ValueError("Remote evaluation needs a remote backend")
    if len(config.data_inputs) != 1:
        raise ValueError("A single complete joined manifest is required")
    reference = config.data_inputs[0]
    if not reference.manifest_id:
        raise ValueError("DATA_SNAPSHOT_REQUIRED")
    frame = load_frozen_market_frame(reference.manifest_id, snapshot_root)
    digest = frame.attrs["data_snapshot"]["content_hash"].removeprefix("sha256:")
    if reference.content_sha256 != digest:
        raise ValueError("DATA_SNAPSHOT_CONTENT_MISMATCH")
    return frame


def evaluation_summary(row: Experiment) -> dict:
    return {"evaluation_id": row.experiment_id, "project_id": str(row.project_id),
            "definition_hash": row.definition_hash, "evaluation_hash": row.evaluation_hash,
            "backend": row.backend, "status": row.status, "evidence_status": row.evidence_status or "legacy_unverified",
            "summary": row.result_summary, "failure_reason": row.failure_reason}


def serialize_frame(frame) -> list:
    """Normalize finite JSON at the artifact boundary; no DataFrame index identity."""
    import json

    return json.loads(frame.to_json(orient="records", date_format="iso"))


def verify_frozen_config(row: Experiment) -> EvaluationConfigV1:
    payload = row.evaluation_config or {}
    config = EvaluationConfigV1.model_validate(payload["config"])
    if "strategy" in payload:
        from quantgpt.research.contracts import strategy_hash
        from quantgpt.strategy.spec import StrategySpecV2

        spec = StrategySpecV2.model_validate(payload["strategy"])
        expected = strategy_hash(spec.model_dump(mode="json"), config, spec.factor_evaluations)
    else:
        definition = FactorDefinitionV1.model_validate(payload["definition"])
        expected = evaluation_hash(definition, config)
    if expected != row.evaluation_hash:
        raise ValueError("EVALUATION_CONFIG_INTEGRITY_MISMATCH")
    canonical_json(payload)
    return config


async def evaluate_factor_batch(session: AsyncSession, actor_id: uuid.UUID, project_id: uuid.UUID,
                                 definitions: list[FactorDefinitionV1], config: EvaluationConfigV1,
                                 *, snapshot_root: str | Path, cancel_check=None, task_context: dict | None = None) -> dict:
    """One verified panel per batch; retries return saved summaries without compute.

    This runner records research evidence. Promotion remains fail-closed until the
    applicable profile has all independently generated checks.
    """
    import asyncio
    import math

    import pandas as pd

    from quantgpt.backtest import api_context, run_factor_backtest
    from quantgpt.expression_parser import extract_components
    from quantgpt.research.runtime import normalize_local_config
    from quantgpt.task_store import CancelledException

    await require_project_member(session, actor_id, project_id, write=True)
    if not definitions or len(definitions) > 100:
        raise ValueError("Batch size must be between 1 and 100")
    if config.backend != "local" or config.simulation_config is None:
        raise ValueError("Local factor runner requires local simulation config")
    config = normalize_local_config(config)
    assert config.simulation_config is not None
    if config.window.label_horizon_sessions != config.simulation_config.rebalance_every_sessions:
        raise ValueError("LABEL_HORIZON_UNSUPPORTED: label horizon must match the current holding period")
    await authorize_data_inputs(session, project_id, config)
    if any(definition.language != "local" for definition in definitions):
        raise ValueError("Local runner cannot evaluate WQ expressions")
    for definition in definitions:
        undeclared = extract_components(definition.expression)["fields"] - {field.name for field in definition.fields}
        if undeclared:
            raise ValueError(f"FIELD_CONTRACT_REQUIRED: {sorted(undeclared)}")
    if config.window.phase == "final" and len(definitions) != 1:
        raise ValueError("Only one frozen candidate may expose a final window")
    records = []
    for definition in definitions:
        row, _ = await register_evaluation(session, actor_id, project_id, definition, config)
        await guard_evaluation_window(session, actor_id, row, config)
        records.append((definition, row))
    # Holdout reservation is durable before metrics can be observed or a worker dies.
    await session.commit()
    panel = None
    panel_builds = 0
    engine_calls = 0
    for definition, row in records:
        if cancel_check:
            cancel_check()
        await require_project_member(session, actor_id, project_id, write=True)
        if row.result_summary is not None:
            continue
        try:
            if panel is None:
                panel = load_evaluation_panel(config, snapshot_root)
                dates = pd.to_datetime(panel["trade_date"])
                # Never pass the held-out future to parsing, grouping or scoring.
                panel = cast(pd.DataFrame, panel[dates <= pd.Timestamp(config.window.end_session)].copy())
                panel_builds += 1
            missing_fields = sorted({field.name for field in definition.fields} - set(panel.columns))
            if missing_fields:
                raise ValueError(f"CAPABILITY_FIELD_UNAVAILABLE: {missing_fields}")
            simulation = config.simulation_config
            assert simulation is not None
            prepared_panel = cast(pd.DataFrame, panel)

            def calculate(simulation: SimulationConfigV1 = simulation):
                with api_context():
                    return run_factor_backtest(prepared_panel, expression=definition.expression,
                        holding_period=simulation.rebalance_every_sessions,
                        cost_rate=(simulation.fees_bps + simulation.slippage_bps) / 10000,
                        neutralize_industry="industry" in config.neutralization,
                        neutralize_cap="market_cap" in config.neutralization,
                        direction_mode="fixed", fixed_direction=1 if config.direction == "higher_is_better" else -1,
                        rebalance_anchor=simulation.rebalance_anchor_session.isoformat(), simulation_config=simulation,
                        evaluation_start=config.window.start_session.isoformat(),
                        evaluation_end=config.window.end_session.isoformat())

            engine_calls += 1
            output = await asyncio.to_thread(calculate)
            if cancel_check:
                cancel_check()
            await assert_publication_attempt(session, task_context)
            await require_project_member(session, actor_id, project_id, write=True)
            metrics = {key: value for key, value in output.items()
                       if not key.startswith("_") and isinstance(value, (int, float, str, bool))
                       and (not isinstance(value, float) or math.isfinite(value))}
            from quantgpt.research.cards import build_factor_research_card
            from quantgpt.research.hypotheses import get_hypothesis
            from quantgpt.research.validation import observed_trial_count

            hypothesis = await get_hypothesis(session, actor_id, project_id, definition_hash(definition))
            card = build_factor_research_card(evaluation_id=str(row.experiment_id), definition=definition,
                config=config, output=output, hypothesis_registration=hypothesis,
                trial_count=await observed_trial_count(session, project_id) + 1)
            returns = output["strategy_returns"].rename("return").rename_axis("session").reset_index()
            artifacts = [await write_artifact(session, row, "returns", serialize_frame(returns))]
            if "_factor_df" in output:
                artifacts.append(await write_artifact(session, row, "factor_values", serialize_frame(output["_factor_df"])))
            artifacts.append(await write_artifact(session, row, "research_card", card))
            row.result_summary = {"metrics": metrics, "artifacts": artifacts, "performance_observed": True,
                                  "phase": config.window.phase, "research_decision": "not_evaluated",
                                  "blockers": ["VALIDATION_PROFILE_INCOMPLETE"],
                                  "engine_used": "python", "semantics_version": config.engine.semantics_version}
            row.status = "backtested_train" if config.window.phase == "selection" else "validated_oos"
            row.evidence_status = "research_only"
        except CancelledException:
            raise
        except Exception as exc:
            row.failure_reason = str(exc)
            row.status = "rejected"
            # Execution failures are distinct from observed rejected research trials.
            row.result_summary = {"performance_observed": False, "research_decision": "insufficient_data",
                                  "error_code": "EVALUATION_FAILED", "retryable": False,
                                  "next_action": "resolve_reported_data_or_contract_blocker"}
        await session.commit()
        if cancel_check:
            cancel_check()
    return {"evaluations": [evaluation_summary(row) for _, row in records],
            "config": config.model_dump(mode="json"),
            "batch_diagnostics": {"panel_builds": panel_builds, "engine_calls": engine_calls}}
