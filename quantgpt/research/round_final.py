"""Freeze the selected strategy and publish one canonical validation for a round."""

from __future__ import annotations

import asyncio
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import cast

import pandas as pd
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from quantgpt.models import (
    EvaluationExecutionLease,
    Experiment,
    ExperimentArtifact,
    FinalWindowExposure,
    ResearchRound,
    Task,
)
from quantgpt.research.contracts import EvaluationConfigV1, content_hash, strategy_hash
from quantgpt.research.evaluations import assert_publication_attempt, authorize_data_inputs, load_evaluation_panel
from quantgpt.research.legacy_history import legacy_window_blocker, review_legacy_windows, round_legacy_trials
from quantgpt.research.runtime import local_engine_identity
from quantgpt.research.runtime import snapshot_root as default_snapshot_root
from quantgpt.research.strategies import get_strategy_run, run_strategy_evaluation
from quantgpt.research.validation import build_trial_manifest
from quantgpt.research.validation_runner import MIN_SESSIONS, run_local_validation
from quantgpt.strategy.spec import StrategySpecV2
from quantgpt.task_store import TERMINAL_TASK_STATUSES


async def check_preround_work(session: AsyncSession, project_id: uuid.UUID, *, opening: bool) -> None:
    """Called under the project admission lock. Durable requests and synchronous work both count."""
    pending = await session.scalar(select(Task.id).where(Task.project_id == project_id,
        Task.task_type == "research_evaluation", Task.params["round_id"].as_string().is_(None),
        Task.params["kind"].as_string().in_(["factor", "strategy"]),
        Task.params["config"]["window"]["phase"].as_string() == "selection",
        Task.status.not_in(TERMINAL_TASK_STATUSES)).limit(1))
    # At freeze all round tasks have drained, so any outstanding selection
    # lease with unpublished results also belongs to outside work.
    active = await session.scalar(select(EvaluationExecutionLease.evaluation_id).join(
        Experiment, Experiment.experiment_id == EvaluationExecutionLease.evaluation_id).where(
        Experiment.project_id == project_id, EvaluationExecutionLease.expires_at > datetime.now(timezone.utc),
        Experiment.evaluation_config["config"]["window"]["phase"].as_string() == "selection",
        Experiment.result_summary["performance_observed"].as_boolean().is_(None)).limit(1))
    if pending or active:
        action = "opening" if opening else "freezing"
        raise ValueError(f"PREROUND_SELECTION_PENDING: finish or cancel earlier selection work before {action} the round")


def _frozen_record(row: ResearchRound) -> dict:
    frozen = row.frozen or {}
    record = {key: value for key, value in frozen.items() if key != "freeze_hash"}
    if (not row.final_evaluation_id or record.get("final_evaluation_id") != row.final_evaluation_id
            or frozen.get("freeze_hash") != content_hash("quantgpt.round_freeze/v1", record)):
        raise ValueError("ROUND_FREEZE_INTEGRITY_MISMATCH")
    return frozen


def _final_config(selection: dict, holdout: dict) -> EvaluationConfigV1:
    return EvaluationConfigV1.model_validate({**selection, "window": {**selection["window"], "phase": "final",
        "start_session": holdout["start"], "end_session": holdout["end"]}})


def _window_coverage(config: EvaluationConfigV1, observed: set[date], declared: set[date]) -> dict:
    window = config.window
    phase = window.phase.upper()
    start, end = window.start_session, window.end_session
    if config.scope.calendar_id == "synthetic_weekdays":
        expected = [value.date() for value in pd.bdate_range(start, end).tolist()]
    else:
        import exchange_calendars

        name = config.scope.calendar_id
        if name in exchange_calendars.get_calendar_names():
            calendar = exchange_calendars.get_calendar(name, start=start - timedelta(days=14),
                                                       end=end + timedelta(days=14))
            expected = [value.date() for value in calendar.sessions_in_range(start, end).tolist()]
        else:
            if start < min(declared) or end > max(declared):
                raise ValueError(f"{phase}_WINDOW_OUTSIDE_PUBLISHED_CALENDAR")
            expected = sorted(day for day in declared if start <= day <= end)
    if not declared.issuperset(expected) or not observed.issuperset(expected):
        raise ValueError(f"{phase}_SNAPSHOT_SESSION_GAP")
    required = window.label_horizon_sessions + 1
    if window.phase == "final":
        required = max(3 * MIN_SESSIONS, required)
    if len(expected) < required:
        raise ValueError(f"{phase}_WINDOW_INSUFFICIENT_SESSIONS: {len(expected)} available, {required} required")
    if sum(day < expected[0] for day in observed & declared) < window.warmup_observations:
        raise ValueError(f"{phase}_SNAPSHOT_WARMUP_INSUFFICIENT")
    return {"scoring_sessions": len(expected), "first_scoring_session": expected[0].isoformat(),
            "last_scoring_session": expected[-1].isoformat(), "required_sessions": required}


def check_final_session_coverage(config: EvaluationConfigV1, root: Path, *,
                                 selection_config: EvaluationConfigV1 | None = None) -> dict:
    """Check immutable market session keys without computing any signal, price or return."""
    if selection_config is not None and (selection_config.data_inputs != config.data_inputs
            or selection_config.scope != config.scope or selection_config.window.phase != "selection"):
        raise ValueError("ROUND_SELECTION_CONFIG_MISMATCH")
    panel = load_evaluation_panel(config, root)
    try:
        observed = {date.fromisoformat(str(value)[:10]) for value in panel["trade_date"].unique()}
        calendar = [date.fromisoformat(str(value)[:10]) for value in panel.attrs.get("calendar_sessions", [])]
    except ValueError as exc:
        raise ValueError("FINAL_SESSION_CALENDAR_UNAVAILABLE") from exc
    declared = set(calendar)
    if not observed or not declared or len(calendar) != len(declared):
        raise ValueError("FINAL_SESSION_CALENDAR_UNAVAILABLE")
    coverage = _window_coverage(config, observed, declared)
    if selection_config is not None:
        coverage["selection_coverage"] = _window_coverage(selection_config, observed, declared)
    return coverage


async def final_validation_context(session: AsyncSession, project_id: uuid.UUID, evaluation_id: str) -> dict | None:
    row = await session.scalar(select(ResearchRound).where(
        ResearchRound.project_id == project_id, ResearchRound.final_evaluation_id == evaluation_id))
    if row is None:
        return None
    frozen = _frozen_record(row)
    manifest = frozen["trial_manifest"]
    review = manifest.get("legacy_window_review")
    if review is None:
        # Archived v1 freezes never recorded parent trials or checked overlap.
        # Never promote their historic pass without reconstructing the original
        # search evidence. Preserve all frozen identities when originals are gone.
        from quantgpt.research.rounds import round_directions

        directions = await round_directions(session, row.id)
        if not manifest.get("legacy_trials") and not any(branch.legacy_sources for branch in directions):
            return {"round_id": str(row.id), "selection_evaluation_id": frozen["selection_evaluation_id"],
                    "trial_manifest": manifest}
        unresolved: set[str] = set()
        legacy = await round_legacy_trials(session, directions, row.project_id, unresolved_sources=unresolved)
        old_keys = set(manifest["trial_keys"])
        recorded = {r["trial_key"] for r in legacy}
        for key in old_keys - recorded:
            if key.startswith("legacy:"):
                legacy.append({"trial_key": key})
        review = review_legacy_windows(legacy, row.selection_config, row.holdout, unresolved_sources=unresolved)
        keys = old_keys | recorded
        revised = {**manifest, "trial_keys": sorted(keys), "observed_trials": len(keys),
                   "legacy_trials": len({t["trial_key"] for t in legacy if not t["trial_key"].startswith("evaluation:")}),
                   "legacy_window_review": review, "original_manifest_hash": manifest["manifest_hash"]}
        revised.pop("manifest_hash")
        manifest = {**revised, "manifest_hash": content_hash("quantgpt.trial_manifest/v1", revised)}
    return {"round_id": str(row.id), "selection_evaluation_id": frozen["selection_evaluation_id"],
            "trial_manifest": manifest}


async def finalize_round(session: AsyncSession, actor_id: uuid.UUID, project_id: uuid.UUID, *, round_id: uuid.UUID,
                         coordinator_key: str, selection_run_id: uuid.UUID) -> dict:
    from quantgpt.research.rounds import (
        check_components,
        enqueue,
        get_round,
        lock_round,
        require_coordinator,
        round_directions,
        round_tasks,
    )

    inspected = await get_round(session, actor_id, project_id, round_id)
    require_coordinator(inspected, actor_id, coordinator_key)
    coverage = None
    if inspected.status == "open":
        if any(not d.finished for d in await round_directions(session, round_id)):
            raise ValueError("ROUND_DIRECTIONS_UNFINISHED")
        config = _final_config(inspected.selection_config, inspected.holdout)
        await authorize_data_inputs(session, project_id, config)
        # Release the read transaction before snapshot I/O; repeat the admission
        # checks under the project lock before freezing the manifest.
        await session.commit()
        coverage = await asyncio.to_thread(check_final_session_coverage, config, default_snapshot_root())
    row = await lock_round(session, actor_id, project_id, round_id)
    require_coordinator(row, actor_id, coordinator_key)
    if row.status in {"frozen", "completed"}:
        frozen = _frozen_record(row)
        if frozen["selection_run_id"] != str(selection_run_id):
            raise ValueError("ROUND_FINAL_ALREADY_FROZEN")
    else:
        if row.status != "open":
            raise ValueError("ROUND_NOT_OPEN")
        directions = await round_directions(session, round_id)
        if not directions or any(not direction.finished for direction in directions):
            raise ValueError("ROUND_DIRECTIONS_UNFINISHED")
        tasks = await round_tasks(session, round_id)
        # Interrupted or uncertain work can still produce another observation.
        if any(str(task.status) not in {"completed", "failed", "cancelled"} for task in tasks):
            raise ValueError("ROUND_TASKS_PENDING")
        await check_preround_work(session, project_id, opening=False)
        chosen = [task for task in tasks if (task.params or {}).get("kind") == "strategy"
                  and str(task.status) == "completed" and (task.result or {}).get("strategy_run_id") == str(selection_run_id)]
        if not chosen:
            raise ValueError("ROUND_COMPLETED_SELECTION_STRATEGY_REQUIRED")
        if await session.scalar(select(FinalWindowExposure.project_id).where(
                FinalWindowExposure.project_id == project_id).limit(1)):
            raise ValueError("FINAL_WINDOW_ALREADY_EXPOSED")
        run = await get_strategy_run(session, actor_id, project_id, selection_run_id)
        saved = cast(dict, run.result or {})
        if saved.get("scope") != "selection" or saved.get("config") != row.selection_config:
            raise ValueError("ROUND_SELECTION_CONFIG_MISMATCH")
        spec = StrategySpecV2.model_validate(saved["spec"])
        await check_components(session, row, spec)
        config = _final_config(saved["config"], row.holdout)
        if config.engine != local_engine_identity():
            raise ValueError("CODE_VERSION_CHANGED: final requires the frozen selection engine")
        final_hash = strategy_hash(saved["spec"], config, spec.factor_evaluations)
        final_run_id = uuid.uuid5(project_id, "strategy:" + final_hash)
        evaluation_ids = (await session.scalars(select(Experiment.experiment_id).where(
            Experiment.project_id == project_id,
            Experiment.evaluation_config["config"]["window"]["phase"].as_string() == "selection"))).all()
        unresolved: set[str] = set()
        legacy = await round_legacy_trials(session, directions, row.project_id, unresolved_sources=unresolved)
        review = review_legacy_windows(legacy, row.selection_config, row.holdout, unresolved_sources=unresolved)
        blocker = legacy_window_blocker(review)
        if blocker:
            raise ValueError(f"{blocker}: {review}; verify archived source dates or choose an independent holdout")
        manifest = await build_trial_manifest(session, project_id, evaluation_ids, legacy_trials=legacy, legacy_window_review=review)
        if saved["evaluation_id"] not in manifest["evaluation_ids"]:
            raise ValueError("ROUND_SELECTION_OBSERVATION_REQUIRED")
        record = {"schema_version": "research_round_freeze/v1", "round_id": str(round_id),
                  "selection_run_id": str(selection_run_id), "selection_evaluation_id": saved["evaluation_id"],
                  "selection_hash": saved["evaluation_hash"], "selection_task_ids": sorted(str(t.id) for t in tasks),
                  "trial_manifest": manifest, "spec": saved["spec"], "config": config.model_dump(mode="json"),
                  "session_coverage": coverage,
                  "final_evaluation_id": "se_" + final_run_id.hex, "final_run_id": str(final_run_id), "final_hash": final_hash}
        frozen = {**record, "freeze_hash": content_hash("quantgpt.round_freeze/v1", record)}
        row.final_evaluation_id = frozen["final_evaluation_id"]
        row.frozen, row.status = frozen, "frozen"
        await session.flush()
    params = {"kind": "round_final", "round_id": str(round_id), "config": frozen["config"],
              "spec": frozen["spec"], "server_config_frozen": True}
    accepted = await enqueue(session, actor_id, row, params, f"final:{round_id}")
    if accepted["status"] == "completed":
        from quantgpt.research.validation import archived_validation_notice

        completed_task = await session.get(Task, accepted["task_id"])
        report = (completed_task.result or {}).get("validation") if completed_task else None
        if isinstance(report, dict) and (notice := archived_validation_notice(report)):
            accepted["validation_status"] = notice
    if accepted["status"] == "cancelled" and row.status == "frozen":
        task = await session.get(Task, accepted["task_id"], populate_existing=True)
        if (task is None or cast(dict, task.params) != params or str(task.user_id) != str(actor_id)
                or task.project_id != project_id):
            raise ValueError("ROUND_FINAL_TASK_MISMATCH")
        # Coordinator-authenticated retry of the exact frozen request. Clearing
        # the old attempt also fences a still-running cancelled worker.
        await session.execute(update(Task).where(Task.id == task.id, Task.status == "cancelled",
            Task.cancel_requested.is_(True)).values(status="queued", cancel_requested=False, attempt_id=None,
            lease_expires_at=None, retryable=False, result=None, error=None, progress_json={}, dispatch_pending=True,
            revision=Task.revision + 1, updated_at=datetime.now(timezone.utc)))
        await session.commit()
        await session.refresh(task)
        accepted["status"] = str(task.status)
    return accepted


async def execute_final(session: AsyncSession, actor_id: uuid.UUID, project_id: uuid.UUID, params: dict, *,
                        snapshot_root: str | Path | None = None, cancel_check=None, task_context: dict | None = None) -> dict:
    from quantgpt.research.rounds import lock_round

    if task_context is None:
        raise ValueError("ROUND_FINAL_TASK_REQUIRED")
    round_id = uuid.UUID(params["round_id"])
    row = await lock_round(session, actor_id, project_id, round_id)
    frozen = _frozen_record(row)
    task = await session.get(Task, task_context["task_id"], populate_existing=True)
    if (row.status != "frozen" or row.coordinator_id != actor_id or task is None
            or task.project_id != project_id or str(task.user_id) != str(actor_id) or cast(dict, task.params) != params
            or params.get("kind") != "round_final" or params.get("spec") != frozen["spec"]
            or params.get("config") != frozen["config"] or params.get("server_config_frozen") is not True):
        raise ValueError("ROUND_FINAL_TASK_MISMATCH")
    await assert_publication_attempt(session, task_context)
    config = EvaluationConfigV1.model_validate(frozen["config"])
    if config.engine != local_engine_identity():
        raise ValueError("CODE_VERSION_CHANGED: final requires the frozen selection engine")
    context = await final_validation_context(session, project_id, frozen["final_evaluation_id"])
    assert context is not None
    manifest = context["trial_manifest"]
    blocker = legacy_window_blocker(manifest.get("legacy_window_review") or {"status": "clear"})
    if blocker and not await session.scalar(select(FinalWindowExposure.evaluation_id).where(
            FinalWindowExposure.project_id == project_id, FinalWindowExposure.evaluation_id == frozen["final_evaluation_id"])):
        raise ValueError(f"{blocker}: archived selection lacks evidence of an independent final window")
    await session.commit()
    root = snapshot_root if snapshot_root is not None else default_snapshot_root()
    result = await run_strategy_evaluation(session, actor_id, project_id, StrategySpecV2.model_validate(frozen["spec"]),
        config, snapshot_root=root, cancel_check=cancel_check, task_context=task_context)
    if result["evaluation_id"] != frozen["final_evaluation_id"] or result["evaluation_hash"] != frozen["final_hash"]:
        raise ValueError("ROUND_FINAL_RESULT_MISMATCH")
    report = await run_local_validation(session, actor_id, project_id, result["evaluation_id"], snapshot_root=root,
        selection_evaluation_id=frozen["selection_evaluation_id"], cancel_check=cancel_check, task_context=task_context)
    if cancel_check:
        cancel_check()
    row = await lock_round(session, actor_id, project_id, round_id)
    await assert_publication_attempt(session, task_context)
    if _frozen_record(row)["freeze_hash"] != frozen["freeze_hash"]:
        raise ValueError("ROUND_FREEZE_INTEGRITY_MISMATCH")
    artifact = (await session.execute(select(ExperimentArtifact.id, ExperimentArtifact.content_hash).where(
        ExperimentArtifact.experiment_id == result["evaluation_id"], ExperimentArtifact.artifact_type == "offline_validation",
        ExperimentArtifact.payload["report_key"].as_string() == report["report_key"]))).one()
    payload = {**result, "round_id": str(round_id), "selection_run_id": frozen["selection_run_id"],
               "selection_evaluation_id": frozen["selection_evaluation_id"], "trial_manifest": manifest,
               "freeze_hash": frozen["freeze_hash"], "validation": report, "validation_artifact": {
                   "artifact_id": str(artifact.id), "content_sha256": artifact.content_hash,
                   "kind": "offline_validation", "project_id": str(project_id), "evaluation_id": result["evaluation_id"]}}
    # The guarded task terminal state and the round terminal state have one
    # commit, so a cancellation can never leave a completed round with a cancelled job.
    row.status = "completed"
    await session.execute(update(Task).where(Task.id == task_context["task_id"]).values(status="completed",
        result=payload, progress_json={"progress": 100, "stage": "completed"}, lease_expires_at=None,
        retryable=False, dispatch_pending=False, updated_at=datetime.now(timezone.utc), revision=Task.revision + 1))
    await session.commit()
    return payload
