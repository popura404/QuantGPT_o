"""Durable WQ platform evidence; never promotes remote summaries to local proof.

A committed dispatch claim precedes POST. A lost response remains uncertain;
reconciliation with an existing reference performs GET only. This does not claim
platform exactly-once support or invent remote data versions.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import cast

from sqlalchemy import update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from quantgpt.models import Experiment
from quantgpt.research.contracts import (
    EngineIdentity,
    EvaluationConfigV1,
    FactorDefinitionV1,
    content_hash,
)
from quantgpt.research.evaluations import (
    assert_publication_attempt,
    evaluation_summary,
    get_evaluation,
    register_evaluation,
    verify_frozen_config,
    write_artifact,
)
from quantgpt.research.projects import require_project_member
from quantgpt.wq_brain_client import WQBrainClient

_SETTINGS = {"region", "universe", "delay", "decay", "neutralization", "truncation"}
_FIXED_SETTINGS = WQBrainClient.simulation_payload("")["settings"]
_TERMINAL = {"completed", "failed", "remote_cancel_confirmed"}


def capability_report() -> dict:
    """Implementation capabilities, explicitly distinct from an external probe."""
    return {
        "backend": "wq", "external_integration": "not_run", "evidence_scope": "platform_only",
        "bottom_data_version": "unknown", "remote_idempotency": "unknown",
        "request_fingerprint_lookup": "unknown", "remote_cancellation": "unknown",
        "custom_evaluation_dates": "unknown", "resume_by_remote_reference": "implemented",
        "raw_returns_for_local_statistics": "unavailable", "local_strategy_evidence": False,
        "field_mapping": [
            {"local": "close", "remote": "close", "equivalence": "unverified"},
            {"local": "volume", "remote": "volume", "equivalence": "unverified"},
            {"local": "market_cap", "remote": "cap", "equivalence": "unverified"},
            {"local": "industry", "remote": "industry", "equivalence": "unverified"},
            {"local": None, "remote": "subindustry", "equivalence": "remote_only"},
        ],
        "operator_mapping": [
            {"local": "rank", "remote": "rank", "equivalence": "unverified"},
            {"local": "ts_mean", "remote": "ts_mean", "equivalence": "unverified"},
            {"local": "scale", "remote": "scale", "equivalence": "different_or_unknown"},
        ],
    }


def normalize_wq_config(config: EvaluationConfigV1) -> EvaluationConfigV1:
    if config.backend != "wq":
        raise ValueError("WQ_BACKEND_REQUIRED")
    settings = dict(config.remote_settings)
    for key in settings.keys() - _SETTINGS:
        if key not in _FIXED_SETTINGS or settings[key] != _FIXED_SETTINGS[key]:
            raise ValueError(f"UNSUPPORTED_REMOTE_SETTING: {key}")
    arguments = {key: value for key, value in settings.items() if key in _SETTINGS}
    effective = WQBrainClient.simulation_payload("", **arguments)["settings"]
    if config.scope.universe_id != effective["universe"]:
        raise ValueError("REMOTE_UNIVERSE_SCOPE_MISMATCH")
    if effective["region"] == "USA" and config.scope.market not in {"us_equity", "US", "USA"}:
        raise ValueError("REMOTE_MARKET_SCOPE_MISMATCH")
    # Caller claims cannot certify a remote data snapshot or engine version.
    inputs = tuple(item.model_copy(update={"manifest_id": None, "content_sha256": None,
                                          "version_status": "unknown", "remote_run_ref": None})
                   for item in config.data_inputs)
    return config.model_copy(update={
        "remote_settings": effective, "data_inputs": inputs,
        "engine": EngineIdentity(engine="wq", version="unknown", code_version="wq_adapter/v1",
                                 semantics_version=config.engine.semantics_version, conformance="unverified"),
    })


def _state(row: Experiment) -> dict:
    return dict(cast(dict | None, row.result_summary) or {})


def _cancelled(check: Callable[[], bool] | None) -> bool:
    if check is None:
        return False
    from quantgpt.task_store import CancelledException

    try:
        return bool(check())
    except CancelledException:
        return True


def _platform_checks(state: dict, result: dict) -> dict:
    checks = (result.get("is") or {}).get("checks", [])
    valid_checks = (isinstance(checks, list) and bool(checks)
                    and all(isinstance(item, dict) and item.get("name") and item.get("result") == "PASS" for item in checks))
    returned = result.get("settings") or {}
    scope_matches = all(returned.get(key, state["settings"][key]) == state["settings"][key]
                        for key in ("region", "universe", "delay", "neutralization"))
    return {
        "remote_request": {"status": "passed", "fingerprint": state["request_fingerprint"]},
        "remote_run_ref": {"status": "passed", "reference": state["remote_run_ref"]},
        "raw_response": {"status": "passed" if result.get("raw_platform_result") else "unavailable"},
        "platform_checks": {"status": "passed" if valid_checks else "unavailable_or_failed", "checks": checks},
        "platform_scope": {"status": "passed" if scope_matches else "failed", "scope": "platform",
                           "local_strategy_eligible": False},
    }


async def _save(session: AsyncSession, actor_id: uuid.UUID, row: Experiment, previous: set[str],
                status: str, payload: dict, *, artifact: dict | None = None) -> bool:
    if row.project_id is None:
        raise ValueError("Project required")
    await require_project_member(session, actor_id, row.project_id, write=True)
    statement = update(Experiment).where(Experiment.id == row.id, Experiment.status.in_(previous)).values(
        status=status, result_summary=payload, evidence_status="platform_only",
        failure_reason=payload.get("error"), updated_at=datetime.now(timezone.utc),
    ).execution_options(synchronize_session=False)
    changed = cast(CursorResult, await session.execute(statement)).rowcount == 1
    if changed and artifact is not None:
        reference = await write_artifact(session, row, "remote_evidence", artifact)
        payload = {**payload, "artifact_ref": reference}
        await session.execute(update(Experiment).where(Experiment.id == row.id).values(result_summary=payload)
                              .execution_options(synchronize_session=False))
    await session.commit()
    await session.refresh(row)
    return changed


async def evaluate_wq(session: AsyncSession, actor_id: uuid.UUID, project_id: uuid.UUID,
                      definition: FactorDefinitionV1, config: EvaluationConfigV1, *, client: WQBrainClient,
                      cancel_check: Callable[[], bool] | None = None, task_context: dict | None = None) -> dict:
    """Simulate a frozen request once; repeated calls return the saved state."""
    config = normalize_wq_config(config)
    row, created = await register_evaluation(session, actor_id, project_id, definition, config)
    if not created:
        return evaluation_summary(row)
    settings = {key: value for key, value in config.remote_settings.items() if key in _SETTINGS}
    request = WQBrainClient.simulation_payload(definition.expression, **settings)
    request_ref = await write_artifact(session, row, "remote_request", request)
    state = {"schema_version": "remote_evidence/v1", "request_fingerprint": content_hash("wq.request/v1", request),
             "request_ref": request_ref, "observed_at": datetime.now(timezone.utc).isoformat(),
             "remote_run_ref": None, "data_version_status": "unknown", "scope": "platform",
             "independent_final_validation": False, "local_strategy_eligible": False,
             "settings": config.remote_settings, "capabilities": capability_report()}
    await session.commit()  # Identity and request survive a crash before dispatch.
    if _cancelled(cancel_check):
        await _save(session, actor_id, row, {"draft"}, "cancelled", {**state, "cancel_requested": True})
        return evaluation_summary(row)
    if not await _save(session, actor_id, row, {"draft"}, "remote_dispatching", state):
        return evaluation_summary(row)
    await assert_publication_attempt(session, task_context)
    await session.commit()
    try:
        accepted = await asyncio.to_thread(client.start_simulation, definition.expression, **settings)
    except Exception as exc:
        accepted = {"ok": False, "status": "remote_outcome_unknown", "error": str(exc), "retryable": False}
    if not accepted.get("ok"):
        status = accepted.get("status", "remote_outcome_unknown")
        await _save(session, actor_id, row, {"remote_dispatching"}, status, {**state, **accepted}, artifact=accepted)
        return evaluation_summary(row)
    state.update({"remote_run_ref": accepted["remote_run_ref"], "simulation_id": accepted.get("simulation_id")})
    from quantgpt.task_store import record_remote_task_reference

    await record_remote_task_reference(session, task_context, accepted["remote_run_ref"])
    saved = await _save(session, actor_id, row, {"remote_dispatching"}, "remote_polling", state)
    if not saved:
        # Cancellation during POST must still retain the only recoverable remote reference.
        await _save(session, actor_id, row, {"local_wait_cancelled"}, "local_wait_cancelled",
                    {**_state(row), **state, "cancel_requested": True, "remote_cancel_confirmed": False})
        return evaluation_summary(row)
    return await _poll(session, actor_id, row, client=client, cancel_check=cancel_check, task_context=task_context)


async def _poll(session: AsyncSession, actor_id: uuid.UUID, row: Experiment, *, client: WQBrainClient,
                cancel_check: Callable[[], bool] | None, task_context: dict | None = None) -> dict:
    frozen = verify_frozen_config(row)
    state = _state(row)
    payload = row.evaluation_config or {}
    try:
        result = await asyncio.to_thread(client.poll_simulation, state["remote_run_ref"],
                                         expression=payload["definition"]["expression"],
                                         cancel_check=lambda: _cancelled(cancel_check))
    except Exception as exc:
        result = {"ok": False, "status": "remote_outcome_unknown", "error": str(exc),
                  "remote_run_ref": state["remote_run_ref"], "retryable": False, "next_action": "resume_polling"}
    status = result.get("status", "completed" if result.get("ok") else "remote_outcome_unknown")
    if _cancelled(cancel_check) and status != "remote_cancel_confirmed":
        status = "local_wait_cancelled"
    artifact = {"schema_version": "remote_evidence/v1", "remote_run_ref": state["remote_run_ref"],
                "observed_at": datetime.now(timezone.utc).isoformat(), "platform_result": result,
                "data_version_status": "unknown", "requested_window": frozen.window.model_dump(mode="json"),
                "window_control_verified": False, "scope": "platform"}
    summary = {**state, "observed_at": artifact["observed_at"], "remote_cancel_confirmed": False,
               "retryable": False, "next_action": result.get("next_action"), "error": result.get("error")}
    if status == "completed":
        await assert_publication_attempt(session, task_context)
        summary.update({"alpha_id": result.get("alpha_id"), "simulation_id": result.get("simulation_id"),
                        "is_metrics": result.get("is", {}), "oos_metrics": result.get("oos", {}),
                        "performance_observed": True, "validation_checks": _platform_checks(state, result)})
    elif status == "remote_cancel_confirmed":
        summary["remote_cancel_confirmed"] = True
    elif status == "local_wait_cancelled":
        summary["cancel_requested"] = True
    await _save(session, actor_id, row, {"remote_polling"}, status, summary, artifact=artifact)
    return evaluation_summary(row)


async def reconcile_wq_evaluation(session: AsyncSession, actor_id: uuid.UUID, project_id: uuid.UUID,
                                  evaluation_id: str, *, client: WQBrainClient,
                                  cancel_check: Callable[[], bool] | None = None) -> dict:
    row = await get_evaluation(session, actor_id, project_id, evaluation_id)
    await require_project_member(session, actor_id, project_id, write=True)
    if verify_frozen_config(row).backend != "wq":
        raise ValueError("WQ_BACKEND_REQUIRED")
    if str(row.status) in _TERMINAL:
        return evaluation_summary(row)
    state = _state(row)
    if not state.get("remote_run_ref"):
        await _save(session, actor_id, row, {str(row.status)}, "reconciliation_required",
                    {**state, "retryable": False, "next_action": "manual_platform_reconciliation",
                     "error": "No platform lookup or idempotency capability is verified; automatic resubmission blocked"})
        return evaluation_summary(row)
    if not await _save(session, actor_id, row, {str(row.status)}, "remote_polling", state):
        return evaluation_summary(row)
    return await _poll(session, actor_id, row, client=client, cancel_check=cancel_check)


async def cancel_wq_evaluation(session: AsyncSession, actor_id: uuid.UUID, project_id: uuid.UUID,
                               evaluation_id: str) -> dict:
    row = await get_evaluation(session, actor_id, project_id, evaluation_id)
    await require_project_member(session, actor_id, project_id, write=True)
    if verify_frozen_config(row).backend != "wq":
        raise ValueError("WQ_BACKEND_REQUIRED")
    if str(row.status) not in _TERMINAL:
        await _save(session, actor_id, row, {str(row.status)}, "local_wait_cancelled",
                    {**_state(row), "cancel_requested": True, "remote_cancel_confirmed": False,
                     "next_action": "resume_polling", "retryable": False})
    return evaluation_summary(row)
