"""Project-local research rounds. Each durable submission is also a proposal record."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
import uuid
from contextvars import ContextVar
from datetime import date
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from quantgpt.models import Experiment, FinalWindowExposure, ResearchAuditEvent, ResearchDirection, ResearchRound, Task
from quantgpt.research.contracts import (
    EvaluationConfigV1,
    FactorDefinitionV1,
    content_hash,
    definition_hash,
    evaluation_hash,
)
from quantgpt.research.evaluations import authorize_data_inputs, check_factor_simulation_config, register_holdout
from quantgpt.research.hypotheses import lock_research_registration
from quantgpt.research.projects import ProjectAccessError, require_project_member
from quantgpt.research.round_final import check_final_session_coverage, check_preround_work
from quantgpt.research.runtime import normalize_local_config, snapshot_root
from quantgpt.research.strategies import check_factor_lineage, get_strategy_run
from quantgpt.strategy.spec import StrategySpecV2
from quantgpt.task_store import ExistingTaskRequired, submit_durable_task

RUNNING_ROUND: ContextVar[tuple[str, str] | None] = ContextVar("running_research_round", default=None)


class DirectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=160)
    hypothesis: str = Field(min_length=1, max_length=10000)
    actor_id: uuid.UUID | None = None


def _digest(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _direction_key(master: str, direction_id: uuid.UUID) -> str:
    return hmac.new(master.encode(), direction_id.bytes, hashlib.sha256).hexdigest()


def require_coordinator(row: ResearchRound, actor_id: uuid.UUID, key: str) -> None:
    if row.coordinator_id != actor_id or not hmac.compare_digest(row.coordinator_digest, _digest(key)):
        raise ProjectAccessError("Coordinator credential required")


async def get_round(session: AsyncSession, actor_id: uuid.UUID, project_id: uuid.UUID,
                    round_id: uuid.UUID) -> ResearchRound:
    await require_project_member(session, actor_id, project_id)
    row = await session.get(ResearchRound, round_id, populate_existing=True)
    if row is None or row.project_id != project_id:
        raise ProjectAccessError("Round not found or inaccessible")
    return row


async def lock_round(session: AsyncSession, actor_id: uuid.UUID, project_id: uuid.UUID,
                     round_id: uuid.UUID) -> ResearchRound:
    # Admission, declaring a direction finished, and sealing the manifest share
    # this short DB transaction. Workers do their numerical work outside it.
    await lock_research_registration(session, actor_id, project_id)
    return await get_round(session, actor_id, project_id, round_id)


async def round_directions(session: AsyncSession, round_id: uuid.UUID) -> list[ResearchDirection]:
    return list(await session.scalars(select(ResearchDirection).where(ResearchDirection.round_id == round_id)
                                      .order_by(ResearchDirection.name).execution_options(populate_existing=True)))


async def round_tasks(session: AsyncSession, round_id: uuid.UUID) -> list[Task]:
    return list(await session.scalars(select(Task).where(Task.task_type == "research_evaluation",
        Task.params["round_id"].as_string() == str(round_id)).order_by(Task.id)
        .execution_options(populate_existing=True)))


async def _proposal_evidence(session: AsyncSession, tasks: list[Task]) -> dict[str, list[dict]]:
    """Keep attribution even when a batch is cancelled after publishing its first candidate."""
    by_task: dict[str, list[dict]] = {}
    identities = set()
    for task in tasks:
        params = cast(dict, task.params)
        if params["kind"] != "factor":
            continue
        config = EvaluationConfigV1.model_validate(params["config"])
        proposals = []
        for item in params["definitions"]:
            definition = FactorDefinitionV1.model_validate(item)
            digest = evaluation_hash(definition, config)
            assert task.project_id is not None
            eid = "ev_" + uuid.uuid5(task.project_id, digest).hex
            proposals.append({"expression": definition.expression, "evaluation_id": eid,
                              "evaluation_hash": digest, "definition_hash": definition_hash(definition),
                              "project_id": str(task.project_id), "backend": config.backend, "scope": config.window.phase})
            identities.add(eid)
        by_task[str(task.id)] = proposals
    if identities:
        summary = Experiment.result_summary
        observed = (await session.execute(select(Experiment.experiment_id, Experiment.status,
            summary["performance_observed"].as_boolean(), summary["metrics"], Experiment.failure_reason)
            .where(Experiment.experiment_id.in_(identities)))).all()
        evidence = {eid: {"status": state, "performance_observed": bool(seen), "metrics": metrics or {},
                          "failure_reason": reason} for eid, state, seen, metrics, reason in observed}
        for proposals in by_task.values():
            for proposal in proposals:
                proposal.update(evidence.get(proposal["evaluation_id"], {"performance_observed": False}))
    return by_task


async def round_evaluation_ids(session: AsyncSession, round_id: uuid.UUID) -> set[str]:
    by_task = await _proposal_evidence(session, await round_tasks(session, round_id))
    return {p["evaluation_id"] for proposals in by_task.values() for p in proposals if p["performance_observed"]}


async def guard_round_execution(session: AsyncSession, project_id: uuid.UUID, config: EvaluationConfigV1,
                                evaluation_id: str) -> None:
    row = await session.scalar(select(ResearchRound).where(ResearchRound.project_id == project_id)
                               .execution_options(populate_existing=True))
    if row is None:
        return
    current = RUNNING_ROUND.get()
    if not current or current[0] != str(row.id):
        raise ValueError("MANAGED_ROUND_TASK_REQUIRED: use the direction or coordinator tool")
    if config.window.phase == "final":
        if current[1] != "round_final" or row.final_evaluation_id != evaluation_id:
            raise ValueError("FROZEN_ROUND_FINAL_REQUIRED")
    elif row.status != "open" or current[1] not in {"factor", "strategy"}:
        raise ValueError("ROUND_CLOSED")


async def create_round(session: AsyncSession, actor_id: uuid.UUID, project_id: uuid.UUID, *, config: dict,
                       directions: list[dict], holdout_start: str | None = None, holdout_end: str | None = None,
                       baseline_run_id: uuid.UUID | None = None, coordinator_key: str | None = None,
                       as_of: str | None = None) -> dict:
    member = await require_project_member(session, actor_id, project_id, write=True)
    if member.role not in {"owner", "admin"}:
        raise ProjectAccessError("Project owner or administrator must open the research round")
    window = config.get("window", {})
    if not isinstance(window, dict):
        raise ValueError("ROUND_WINDOW_TEMPLATE_REQUIRED")
    dates = (window.get("start_session"), window.get("end_session"), holdout_start, holdout_end)
    plan = None
    if all(value is None for value in dates):
        from quantgpt.research.window_policy import plan_project_windows

        plan = await plan_project_windows(session, actor_id, project_id, config=config, as_of=as_of)
        if plan["status"] != "ready":
            return plan
        setup = plan["round_setup"]
        config, holdout_start, holdout_end = setup["config"], setup["holdout_start"], setup["holdout_end"]
    elif any(value is None for value in dates):
        raise ValueError("ROUND_WINDOW_DATES_INCOMPLETE: supply all four selection and holdout dates or omit all four")
    elif as_of is not None:
        raise ValueError("AS_OF_WITH_EXPLICIT_WINDOWS: use as_of only when planning dates automatically")
    assert holdout_start is not None and holdout_end is not None
    effective = normalize_local_config(EvaluationConfigV1.model_validate(config))
    if effective.backend != "local" or effective.window.phase != "selection":
        raise ValueError("LOCAL_SELECTION_CONFIG_REQUIRED")
    check_factor_simulation_config(effective)
    start, end = date.fromisoformat(holdout_start), date.fromisoformat(holdout_end)
    if start >= end:
        raise ValueError("Holdout start must precede end")
    if effective.window.end_session >= start:
        raise ValueError("Selection must end before the final window")
    requested = [DirectionRequest.model_validate(item) for item in directions]
    if not requested or len({item.name for item in requested}) != len(requested):
        raise ValueError("At least one direction and unique direction names are required")
    key = coordinator_key if coordinator_key is not None else secrets.token_urlsafe(32)
    if len(key) < 32:
        raise ValueError("Supply a random coordinator key of at least 32 characters or omit it")
    # Validate the sealed dataset's calendar before registering an immutable window.
    await authorize_data_inputs(session, project_id, effective)
    final_window = {**effective.window.model_dump(mode="json"), "phase": "final",
                    "start_session": start.isoformat(), "end_session": end.isoformat()}
    final_config = EvaluationConfigV1.model_validate({**effective.model_dump(mode="json"), "window": final_window})
    await asyncio.to_thread(check_final_session_coverage, final_config, snapshot_root(), selection_config=effective)
    await lock_research_registration(session, actor_id, project_id)
    await authorize_data_inputs(session, project_id, effective)
    holdout = await register_holdout(session, actor_id, project_id, start=holdout_start, end=holdout_end)
    row = await session.scalar(select(ResearchRound).where(ResearchRound.project_id == project_id))
    if row is not None:
        require_coordinator(row, actor_id, key)
        branches = await round_directions(session, row.id)
        replay_config = effective.model_dump(mode="json")
        if plan is None:
            # An explicit replay returns existing credentials for the configuration frozen by the original server.
            replay_config["engine"] = row.selection_config["engine"]
        if (row.selection_config != replay_config or row.baseline_run_id != baseline_run_id
                or {(b.name, b.hypothesis, b.actor_id) for b in branches}
                != {(d.name, d.hypothesis, d.actor_id or actor_id) for d in requested}):
            raise ValueError("ROUND_ALREADY_CONFIGURED")
        by_name = {b.name: b for b in branches}
        branches = [by_name[d.name] for d in requested]
    else:
        await check_preround_work(session, project_id, opening=True)
        if await session.scalar(select(FinalWindowExposure.evaluation_id).where(
                FinalWindowExposure.project_id == project_id)):
            raise ValueError("FINAL_WINDOW_ALREADY_EXPOSED")
        if baseline_run_id is not None:
            baseline = await get_strategy_run(session, actor_id, project_id, baseline_run_id)
            if (baseline.result or {}).get("config") != effective.model_dump(mode="json"):
                raise ValueError("BASELINE_SELECTION_CONFIG_MISMATCH")
        row = ResearchRound(id=uuid.uuid4(), project_id=project_id, coordinator_id=actor_id,
            coordinator_digest=_digest(key), baseline_run_id=baseline_run_id, holdout=holdout,
            selection_config=effective.model_dump(mode="json"), status="open")
        session.add(row)
        await session.flush()
        branches = []
        for item in requested:
            owner = item.actor_id or actor_id
            await require_project_member(session, owner, project_id, write=True)
            branch_id = uuid.uuid4()
            branch = ResearchDirection(id=branch_id, round_id=row.id, name=item.name, hypothesis=item.hypothesis,
                actor_id=owner, credential_digest=_digest(_direction_key(key, branch_id)))
            session.add(branch)
            branches.append(branch)
        await session.flush()
    return {"project_id": str(project_id), "round_id": str(row.id), "coordinator_key": key,
            **({"window_plan": plan} if plan is not None else {}),
            "discovery_tool": "check_research_factor_novelty", "new_factor_submission": {"only_new": True},
            "directions": [{"direction_id": str(b.id), "name": b.name, "direction_key": _direction_key(key, b.id)}
                           for b in branches]}


async def _branch(session: AsyncSession, row: ResearchRound, actor_id: uuid.UUID, direction_id: uuid.UUID,
                  key: str) -> ResearchDirection:
    branch = await session.get(ResearchDirection, direction_id, populate_existing=True)
    if (branch is None or branch.round_id != row.id or branch.actor_id != actor_id
            or not hmac.compare_digest(branch.credential_digest, _digest(key))):
        raise ProjectAccessError("Direction credential required")
    return branch


async def cancel_round_task(session: AsyncSession, actor_id: uuid.UUID, project_id: uuid.UUID, *,
                            round_id: uuid.UUID, task_id: str,
                            coordinator_key: str | None = None, direction_id: uuid.UUID | None = None,
                            direction_key: str | None = None) -> dict:
    from quantgpt.task_store import _cancel_authorized_task

    row = await lock_round(session, actor_id, project_id, round_id)
    task = await session.get(Task, task_id, populate_existing=True)
    if (task is None or task.project_id != project_id or str(task.task_type) != "research_evaluation"
            or (task.params or {}).get("round_id") != str(round_id)):
        raise ProjectAccessError("Round task or credential unavailable")
    if coordinator_key is not None and direction_key is None and direction_id is None:
        require_coordinator(row, actor_id, coordinator_key)
    elif coordinator_key is None and direction_key is not None and direction_id is not None:
        await _branch(session, row, actor_id, direction_id, direction_key)
        if task.params.get("kind") != "factor" or task.params.get("direction_id") != str(direction_id):
            raise ProjectAccessError("Round task or credential unavailable")
    else:
        raise ProjectAccessError("Round task or credential unavailable")
    result = await _cancel_authorized_task(session, task)
    reply = {"round_id": str(round_id), "task_id": task_id, "status": result["status"], "revision": result["revision"]}
    if task.params.get("kind") == "round_final" and result["status"] == "cancelled":
        reply["resume_tool"] = "finalize_research_round"
        reply["selection_run_id"] = (row.frozen or {})["selection_run_id"]
    return reply


async def enqueue(session: AsyncSession, actor_id: uuid.UUID, row: ResearchRound, params: dict,
                  idempotency_key: str, *, existing_only: bool = False) -> dict:
    payload, created = await submit_durable_task(session, actor_id=str(actor_id), project_id=str(row.project_id),
        task_type="research_evaluation", params={**params, "round_id": str(row.id), "server_config_frozen": True},
        idempotency_key=idempotency_key, existing_only=existing_only)
    return {"round_id": str(row.id), "task_id": payload["task_id"], "status": payload["status"], "created": created,
            "poll_url": f"/api/v1/tasks/{payload['task_id']}"}


async def submit_direction(session: AsyncSession, actor_id: uuid.UUID, project_id: uuid.UUID, *,
                           round_id: uuid.UUID, direction_id: uuid.UUID, direction_key: str,
                           definitions: list[dict], idempotency_key: str | None = None,
                           direction: Literal["higher_is_better", "lower_is_better"] | None = None,
                           parent_evaluation_id: str | None = None, only_new: bool = False) -> dict:
    row = await lock_round(session, actor_id, project_id, round_id)
    branch = await _branch(session, row, actor_id, direction_id, direction_key)
    if row.status != "open":
        raise ValueError("DIRECTION_CLOSED")
    if not 1 <= len(definitions) <= 100:
        raise ValueError("Batch size must be in [1, 100]")
    config = EvaluationConfigV1.model_validate(row.selection_config)
    if direction is not None:
        config = EvaluationConfigV1.model_validate({**row.selection_config, "direction": direction})
    if normalize_local_config(config).engine != config.engine:
        raise ValueError("CODE_VERSION_CHANGED: restore the round's frozen engine")
    if parent_evaluation_id and parent_evaluation_id not in await round_evaluation_ids(session, row.id):
        raise ValueError("PARENT_EVALUATION_OUTSIDE_ROUND")
    candidates = [FactorDefinitionV1.model_validate(item) for item in definitions]
    for candidate in candidates:
        if candidate.language != "local":
            raise ValueError("LOCAL_FACTOR_DEFINITION_REQUIRED")
        evaluation_hash(candidate, config)
    params = {"kind": "factor", "config": config.model_dump(mode="json"), "direction_id": str(branch.id),
              "definitions": [d.model_dump(mode="json") for d in candidates], "parent_evaluation_id": parent_evaluation_id}
    identity = idempotency_key or content_hash("quantgpt.direction_request/v1", params)
    request_key = f"direction:{branch.id}:{identity}"
    if only_new and not branch.finished:
        # Under the project lock the next direction sees our task before it can
        # queue its own proposal. An exact retry must still return our original task.
        try:
            return await enqueue(session, actor_id, row, params, request_key, existing_only=True)
        except ExistingTaskRequired:
            pass
        from quantgpt.research.novelty import check_factor_novelty

        report = await check_factor_novelty(session, actor_id, project_id,
                                            expressions=[definition.expression for definition in candidates])
        if len(report["new_indices"]) != len(candidates):
            return {**report, "round_id": str(round_id), "status": "known_candidates", "created": False,
                    "next_action": "submit_new_indices_or_choose_another_expression"}
    return await enqueue(session, actor_id, row, params, request_key, existing_only=branch.finished)


async def finish_direction(session: AsyncSession, actor_id: uuid.UUID, project_id: uuid.UUID, *,
                           round_id: uuid.UUID, direction_id: uuid.UUID, direction_key: str | None = None,
                           coordinator_key: str | None = None) -> dict:
    row = await lock_round(session, actor_id, project_id, round_id)
    if coordinator_key is not None and direction_key is None:
        require_coordinator(row, actor_id, coordinator_key)
        branch = await session.get(ResearchDirection, direction_id, populate_existing=True)
        if branch is None or branch.round_id != row.id:
            raise ProjectAccessError("Direction unavailable")
    elif direction_key is not None and coordinator_key is None:
        branch = await _branch(session, row, actor_id, direction_id, direction_key)
    else:
        raise ProjectAccessError("A single direction or coordinator credential is required")
    if not branch.finished:
        branch.finished = True
        session.add(ResearchAuditEvent(project_id=project_id, actor_id=actor_id, action="round_direction_finished",
            payload={"round_id": str(row.id), "direction_id": str(branch.id),
                     "authority": "coordinator" if coordinator_key else "direction"}))
        await session.flush()
    return {"direction_id": str(branch.id), "finished": True}


async def import_iteration(session: AsyncSession, actor_id: uuid.UUID, project_id: uuid.UUID, *,
                           round_id: uuid.UUID, direction_id: uuid.UUID, direction_key: str, task_id: str) -> dict:
    from quantgpt.research.legacy_history import merge_trials, review_legacy_windows
    from quantgpt.search_ledger import IncompleteIterationHistory, load_observed_iteration_trials

    row = await lock_round(session, actor_id, project_id, round_id)
    branch = await _branch(session, row, actor_id, direction_id, direction_key)
    existing = next((s for s in branch.legacy_sources if s["task_id"] == task_id), None)
    if row.status != "open" or (branch.finished and not existing):
        raise ValueError("DIRECTION_CLOSED")
    blocker = None
    try:
        current = await load_observed_iteration_trials(session, actor_id, task_id, project_id=project_id)
    except IncompleteIterationHistory as exc:
        current, blocker = exc.trials, str(exc)
    trials = merge_trials(existing["trials"] if existing else [], current)
    review = review_legacy_windows(trials, row.selection_config, row.holdout, unresolved_sources=[task_id] if blocker else [])
    exposed = set(review["overlapping_or_later_trials"])
    trials = [{**trial, "exposed_holdout": row.holdout} if trial["trial_key"] in exposed else trial for trial in trials]
    source = {"schema_version": "legacy_iteration/v2", "task_id": task_id, "trials": trials,
              "holdout_review": review, "history_blocker": blocker}
    if source != existing:
        branch.legacy_sources = [s for s in branch.legacy_sources if s["task_id"] != task_id] + [source]
        await session.flush()
    return {"direction_id": str(branch.id), "task_id": task_id, "observed_legacy_trials": len(trials),
            "holdout_review": review}


async def check_components(session: AsyncSession, row: ResearchRound, spec: StrategySpecV2) -> None:
    allowed = await round_evaluation_ids(session, row.id)
    if row.baseline_run_id:
        baseline = await get_strategy_run(session, row.coordinator_id, row.project_id, row.baseline_run_id)
        baseline_spec = cast(dict, baseline.result)["spec"]
        allowed.update(ref["evaluation_id"] for ref in baseline_spec["factor_evaluations"])
    if any(ref.evaluation_id not in allowed for ref in spec.factor_evaluations):
        raise ValueError("STRATEGY_COMPONENT_OUTSIDE_ROUND")
    await check_factor_lineage(session, row.coordinator_id, row.project_id, spec,
                               EvaluationConfigV1.model_validate(row.selection_config))


async def submit_strategy(session: AsyncSession, actor_id: uuid.UUID, project_id: uuid.UUID, *,
                          round_id: uuid.UUID, coordinator_key: str, spec: dict) -> dict:
    row = await lock_round(session, actor_id, project_id, round_id)
    require_coordinator(row, actor_id, coordinator_key)
    if row.status != "open":
        raise ValueError("ROUND_CLOSED")
    candidate = StrategySpecV2.model_validate(spec)
    await check_components(session, row, candidate)
    params = {"kind": "strategy", "config": row.selection_config, "spec": candidate.model_dump(mode="json")}
    return await enqueue(session, actor_id, row, params, f"strategy:{row.id}:{content_hash('quantgpt.round_spec/v1', params)}")


async def describe_round(session: AsyncSession, actor_id: uuid.UUID, project_id: uuid.UUID, *,
                         round_id: uuid.UUID) -> dict:
    from quantgpt.research.validation import observed_trial_count

    row = await get_round(session, actor_id, project_id, round_id)
    branches = await round_directions(session, round_id)
    tasks = await round_tasks(session, round_id)
    directions = []
    strategy_tasks = []
    final_task = None
    grouped: dict[str, list] = {str(b.id): [] for b in branches}
    all_proposals = await _proposal_evidence(session, tasks)
    for task in tasks:
        params, result = cast(dict, task.params or {}), cast(dict, task.result or {})
        item: dict = {"task_id": str(task.id), "status": str(task.status), "error": task.error,
                      "parent_evaluation_id": params.get("parent_evaluation_id")}
        if params["kind"] == "factor":
            item["proposals"] = all_proposals[str(task.id)]
            item["evaluation_ids"] = [p["evaluation_id"] for p in item["proposals"] if p["performance_observed"]]
            item["direction"] = params["config"]["direction"]
            grouped[params["direction_id"]].append(item)
        else:
            item.update(strategy_run_id=result.get("strategy_run_id"), evaluation_id=result.get("evaluation_id"))
            if params["kind"] == "round_final":
                item["validation_artifact"] = result.get("validation_artifact")
                final_task = item
            else:
                strategy_tasks.append(item)
    for branch in branches:
        proposals = grouped[str(branch.id)]
        directions.append({"direction_id": str(branch.id), "name": branch.name, "hypothesis": branch.hypothesis,
            "actor_id": str(branch.actor_id), "finished": branch.finished, "tasks": proposals,
            "evaluation_ids": sorted({ref for task in proposals for ref in task["evaluation_ids"]}),
            "legacy_task_ids": sorted(s["task_id"] for s in branch.legacy_sources)})
    count = (row.frozen["trial_manifest"]["observed_trials"] if row.frozen
             else await observed_trial_count(session, project_id))
    return {"round_id": str(row.id), "project_id": str(project_id), "status": row.status, "holdout": row.holdout,
            "observed_trials": count, "trial_count_scope": "frozen_round" if row.frozen else "live_project",
            "selection_config": row.selection_config, "baseline_run_id": str(row.baseline_run_id) if row.baseline_run_id else None,
            "directions": directions, "strategy_tasks": strategy_tasks, "final_task": final_task, "frozen": row.frozen}
