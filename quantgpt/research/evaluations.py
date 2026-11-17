"""Project-scoped immutable evaluations and server-owned evidence artifacts.

Legacy factor_hash is preserved on Experiment. New content identities do not
accept caller-supplied success booleans as validation or export authorization.
"""

from __future__ import annotations

import uuid
from datetime import date
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
    DataInputRef,
    EvaluationConfigV1,
    FactorDefinitionV1,
    SimulationConfigV1,
    canonical_json,
    content_hash,
    definition_hash,
    evaluation_hash,
)
from quantgpt.research.evaluation_leases import evaluation_execution, fence_evaluation_execution
from quantgpt.research.market_inputs import check_dynamic_membership, check_signal_availability
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
    await authorize_snapshot_refs(session, project_id, config.data_inputs)


async def authorize_snapshot_refs(session: AsyncSession, project_id: uuid.UUID, refs: tuple[DataInputRef, ...]) -> None:
    """A manifest hash is an identity, never authorization to another project's data."""
    grants = (await session.scalars(select(ResearchAuditEvent.payload).where(
        ResearchAuditEvent.project_id == project_id, ResearchAuditEvent.action == "snapshot_registered",
        ResearchAuditEvent.payload["snapshot_id"].as_string().in_([ref.manifest_id for ref in refs]),
    ))).all()
    for ref in refs:
        matching = [grant for grant in grants if grant.get("snapshot_id") == ref.manifest_id]
        if not matching:
            raise ProjectAccessError("Snapshot not registered in this project")
        # Older grants predate the frozen input contract. Their content digest
        # is still verified when loading the panel.
        frozen = [grant["data_input"] for grant in matching if grant.get("data_input") is not None]
        if frozen and ref.model_dump(mode="json") not in frozen:
            raise ValueError("REGISTERED_DATA_INPUT_MISMATCH")


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


async def guard_unmanaged_selection_publication(session: AsyncSession, actor_id: uuid.UUID, project_id: uuid.UUID,
                                                config: EvaluationConfigV1, evaluation_id: str) -> None:
    from quantgpt.research.hypotheses import lock_research_registration
    from quantgpt.research.rounds import RUNNING_ROUND, guard_round_execution

    if config.window.phase == "selection" and RUNNING_ROUND.get() is None:
        # A synchronous caller can be between registration and lease acquisition
        # when a coordinator opens the round. Recheck at the commit boundary.
        await lock_research_registration(session, actor_id, project_id)
        await guard_round_execution(session, project_id, config, evaluation_id)


async def register_evaluation(session: AsyncSession, actor_id: uuid.UUID, project_id: uuid.UUID,
                              definition: FactorDefinitionV1, config: EvaluationConfigV1) -> tuple[Experiment, bool]:
    await require_project_member(session, actor_id, project_id, write=True)
    identity = evaluation_hash(definition, config)
    evaluation_id = "ev_" + uuid.uuid5(project_id, identity).hex
    from quantgpt.research.hypotheses import lock_research_registration

    await lock_research_registration(session, actor_id, project_id)
    existing = (await session.scalars(select(Experiment).where(Experiment.experiment_id == evaluation_id)
                                      .execution_options(populate_existing=True))).one_or_none()
    if existing is not None:
        return existing, False
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
    artifact = {"artifact_id": str(row.id), "kind": row.artifact_type, "payload": row.payload,
                "content_sha256": row.content_hash, "metadata": row.artifact_metadata}
    if str(row.artifact_type) == "offline_validation" and isinstance(row.payload, dict):
        from quantgpt.research.validation import archived_validation_notice

        if notice := archived_validation_notice(row.payload):
            artifact["validation_status"] = notice
    if str(row.artifact_type) == "research_card":
        from quantgpt.research.cards import read_card_context

        artifact["research_context"] = await read_card_context(session, project_id, str(row.experiment_id),
                                                               (row.artifact_metadata or {}).get("evaluation_hash"))
    return artifact


async def register_holdout(session: AsyncSession, actor_id: uuid.UUID, project_id: uuid.UUID,
                            *, start: str, end: str) -> dict:
    await require_project_member(session, actor_id, project_id, write=True)
    start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)
    if start_date >= end_date:
        raise ValueError("Holdout start must precede end")
    from quantgpt.research.hypotheses import lock_research_registration

    await lock_research_registration(session, actor_id, project_id)
    project = await session.get(ResearchProject, project_id, with_for_update=True, populate_existing=True)
    if project is None:
        raise ProjectAccessError("Project not found")
    requested = {"start": start_date.isoformat(), "end": end_date.isoformat()}
    previous = project.config.get("holdout")
    if isinstance(previous, dict) and (date.fromisoformat(previous["start"]), date.fromisoformat(previous["end"])) == (
            start_date, end_date):
        return requested
    trials = await session.scalar(select(func.count()).select_from(Experiment).where(Experiment.project_id == project_id))
    if previous or trials:
        raise ValueError("Holdout must be registered before research starts and cannot be overwritten")
    project.config = {**project.config, "holdout": requested}
    await session.flush()
    return requested


async def guard_evaluation_window(session: AsyncSession, actor_id: uuid.UUID, evaluation: Experiment,
                                   config: EvaluationConfigV1) -> None:
    """Freeze/expose at project level; changing ID, lineage or snapshot cannot reset it."""
    from quantgpt.research.rounds import guard_round_execution

    if evaluation.project_id is None:
        raise ProjectAccessError("Legacy evaluations cannot expose a research project holdout")
    await require_project_member(session, actor_id, evaluation.project_id, write=True)
    if config.window.phase in {"selection", "final"}:
        await guard_round_execution(session, evaluation.project_id, config, str(evaluation.experiment_id))
    project = await session.get(ResearchProject, evaluation.project_id, populate_existing=True)
    if project is None:
        raise ProjectAccessError("Project not found")
    holdout = project.config.get("holdout")
    start, end = config.window.start_session, config.window.end_session
    if config.window.phase == "selection":
        # A later selection window would still read the holdout as lookback
        # history, so checking only score-window overlap is insufficient.
        if holdout and end >= date.fromisoformat(holdout["start"]):
            raise ValueError("FINAL_WINDOW_WITHHELD: selection and its feature history must precede the holdout")
        return
    if config.window.phase == "platform":
        return
    if not holdout or start != date.fromisoformat(holdout["start"]) or end != date.fromisoformat(holdout["end"]):
        raise ValueError("FINAL_WINDOW_NOT_PREREGISTERED")
    if holdout["start"] != start.isoformat():
        # An older worker compared dates lexically and could have read a
        # noncanonical holdout as selection data. Repairing the spelling must
        # never turn such an exposure into an independent final window.
        window = Experiment.evaluation_config["config"]["window"]
        exposed = await session.scalar(select(Experiment.id).where(Experiment.project_id == project.id,
            window["phase"].as_string() == "selection", window["end_session"].as_string() >= start.isoformat()).limit(1))
        if exposed:
            raise ValueError("FINAL_WINDOW_ALREADY_EXPOSED: historical selection overlaps the registered holdout")
    # Compare-and-swap serializes competing reservations on SQLite as well as PG.
    revision = project.revision
    result = await session.execute(update(ResearchProject).where(ResearchProject.id == project.id,
                                                                 ResearchProject.revision == revision)
                                   .values(revision=revision + 1))
    if cast(CursorResult, result).rowcount != 1:
        raise ValueError("FINAL_RESERVATION_CONFLICT: retry reading current project state")
    key = content_hash("quantgpt.holdout/v1", holdout)  # Preserve the stored identity of older reservations.
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
    # The factor engine's historical column names must refer to the same
    # stable securities and sessions that the strategy and cash ledger use.
    if "security_id" in frame:
        frame["stock_code"] = frame["security_id"]
    if "session" in frame:
        frame["trade_date"] = frame["session"]
    frame.attrs["research_scope"] = config.scope.model_dump(mode="json")
    if "currency" in frame and set(frame["currency"].dropna().unique()) - {config.scope.currency}:
        raise ValueError("FROZEN_PANEL_CURRENCY_MISMATCH")
    check_dynamic_membership(frame, config.scope, config.window.end_session)
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


def check_factor_simulation_config(config: EvaluationConfigV1) -> None:
    simulation = config.simulation_config
    if config.backend != "local" or simulation is None:
        raise ValueError("Local factor runner requires local simulation config")
    if config.window.label_horizon_sessions != simulation.rebalance_every_sessions:
        raise ValueError("LABEL_HORIZON_UNSUPPORTED: label horizon must match the current holding period")
    if simulation.evaluation_start_state != "fresh_cash":
        raise ValueError("CAPABILITY_BLOCKER: factor groups require separate carry-forward state per group")


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
    check_factor_simulation_config(config)
    config = normalize_local_config(config)
    assert config.simulation_config is not None
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
        evaluation_id = str(row.experiment_id)
        async with evaluation_execution(session, evaluation_id, cancel_check=cancel_check, task_context=task_context) as token:
            if token is None:
                await require_project_member(session, actor_id, project_id)
                await session.refresh(row)
                continue
            try:
                if panel is None:
                    panel = await asyncio.to_thread(load_evaluation_panel, config, snapshot_root)
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
                    availability = check_signal_availability(prepared_panel, [*definition.fields, *config.neutralization],
                        calendar_id=config.scope.calendar_id, timezone=config.scope.timezone)
                    with api_context():
                        calculated = run_factor_backtest(prepared_panel, expression=definition.expression,
                            holding_period=simulation.rebalance_every_sessions,
                            cost_rate=(simulation.fees_bps + simulation.slippage_bps) / 10000,
                            neutralize_industry="industry" in config.neutralization,
                            neutralize_cap="market_cap" in config.neutralization,
                            direction_mode="fixed", fixed_direction=1 if config.direction == "higher_is_better" else -1,
                            rebalance_anchor=simulation.rebalance_anchor_session.isoformat(), simulation_config=simulation,
                            evaluation_start=config.window.start_session.isoformat(),
                            evaluation_end=config.window.end_session.isoformat())
                    return calculated, availability

                engine_calls += 1
                output, availability = await asyncio.to_thread(calculate)
            except CancelledException:
                raise
            except (ValueError, KeyError, ArithmeticError) as exc:
                await guard_unmanaged_selection_publication(session, actor_id, project_id, config, evaluation_id)
                await fence_evaluation_execution(session, evaluation_id, token)
                await assert_publication_attempt(session, task_context)
                await require_project_member(session, actor_id, project_id, write=True)
                row.failure_reason = str(exc)
                row.status = "rejected"
                # Execution failures are distinct from observed rejected research trials.
                row.result_summary = {"performance_observed": False, "research_decision": "insufficient_data",
                                      "error_code": "EVALUATION_FAILED", "retryable": False,
                                      "next_action": "resolve_reported_data_or_contract_blocker"}
            else:
                if cancel_check:
                    cancel_check()
                await guard_unmanaged_selection_publication(session, actor_id, project_id, config, evaluation_id)
                await fence_evaluation_execution(session, evaluation_id, token)
                await assert_publication_attempt(session, task_context)
                await require_project_member(session, actor_id, project_id, write=True)
                metrics = {key: value for key, value in output.items()
                           if not key.startswith("_") and isinstance(value, (int, float, str, bool))
                           and (not isinstance(value, float) or math.isfinite(value))}
                from quantgpt.research.cards import build_factor_research_card
                from quantgpt.research.hypotheses import get_hypothesis

                hypothesis = await get_hypothesis(session, actor_id, project_id, definition_hash(definition))
                card = build_factor_research_card(evaluation_id=str(row.experiment_id), definition=definition,
                    config=config, output=output, hypothesis_registration=hypothesis)
                returns = output["strategy_returns"].rename("return").rename_axis("session").reset_index()
                artifacts = [await write_artifact(session, row, "returns", serialize_frame(returns))]
                if "_factor_df" in output:
                    artifacts.append(await write_artifact(session, row, "factor_values", serialize_frame(output["_factor_df"])))
                artifacts.append(await write_artifact(session, row, "research_card", card))
                row.result_summary = {"metrics": metrics, "artifacts": artifacts, "performance_observed": True,
                                      "signal_availability": availability,
                                      "phase": config.window.phase, "research_decision": "not_evaluated",
                                      "blockers": ["VALIDATION_PROFILE_INCOMPLETE"],
                                      "engine_used": "python", "semantics_version": config.engine.semantics_version}
                row.status = "backtested_train" if config.window.phase == "selection" else "validated_oos"
                row.evidence_status = "research_only"
            await session.commit()
        if cancel_check:
            cancel_check()
    return {"evaluations": [evaluation_summary(row) for _, row in records],
            "config": config.model_dump(mode="json"),
            "batch_diagnostics": {"panel_builds": panel_builds, "engine_calls": engine_calls}}
