"""Persisted request dispatch shared by HTTP and MCP research entry points."""

from __future__ import annotations

import asyncio
import uuid

from quantgpt.research.contracts import EvaluationConfigV1, FactorDefinitionV1, evaluation_hash
from quantgpt.research.evaluations import evaluate_factor_batch
from quantgpt.research.projects import require_project_member
from quantgpt.research.runtime import local_engine_identity, normalize_local_config, snapshot_root
from quantgpt.research.strategies import run_strategy_evaluation
from quantgpt.strategy.spec import StrategySpecV2


def _validate_request_keys(params: dict, *, durable: bool = False) -> None:
    kind = params.get("kind")
    allowed = {"kind", "config", "definitions" if kind == "factor" else "spec"}
    if durable:
        allowed.add("server_config_frozen")
    if kind not in {"factor", "strategy"}:
        raise ValueError("Unsupported research request kind")
    if set(params) - allowed:
        raise ValueError(f"UNKNOWN_RESEARCH_REQUEST_FIELDS: {sorted(set(params) - allowed)}")


async def execute_research_request(session, actor_id: uuid.UUID, project_id: uuid.UUID,
                                    params: dict, *, cancel_check=None, task_context: dict | None = None) -> dict:
    await require_project_member(session, actor_id, project_id, write=True)
    _validate_request_keys(params, durable=task_context is not None)
    config = EvaluationConfigV1.model_validate(params["config"])
    if task_context is not None:
        if params.get("server_config_frozen") is not True:
            raise ValueError("SERVER_FROZEN_CONFIG_REQUIRED")
        if config.backend == "local" and config.engine != local_engine_identity():
            raise ValueError("CODE_VERSION_CHANGED: accepted engine identity differs from this worker; submit a new request")
    if params["kind"] == "factor":
        definitions = [FactorDefinitionV1.model_validate(item) for item in params["definitions"]]
        if config.backend == "wq":
            from quantgpt.research.wq_backend import evaluate_wq
            from quantgpt.wq_brain_client import get_client

            if len(definitions) != 1:
                raise ValueError("WQ supports one durable remote evaluation per request")
            client = get_client()
            try:
                if not await asyncio.to_thread(client.authenticate):
                    raise ValueError("WQ_AUTHENTICATION_FAILED")
                return await evaluate_wq(session, actor_id, project_id, definitions[0], config,
                                         client=client, cancel_check=cancel_check, task_context=task_context)
            finally:
                client.close()
        return await evaluate_factor_batch(session, actor_id, project_id, definitions, config,
                                            snapshot_root=snapshot_root(), cancel_check=cancel_check, task_context=task_context)
    if params["kind"] == "strategy":
        return await run_strategy_evaluation(session, actor_id, project_id,
            StrategySpecV2.model_validate(params["spec"]), config, snapshot_root=snapshot_root(),
            cancel_check=cancel_check, task_context=task_context)
    raise ValueError("Unsupported research request kind")


async def submit_research_request(session, actor_id: uuid.UUID, project_id: uuid.UUID, params: dict,
                                    idempotency_key: str | None = None) -> dict:
    from quantgpt.task_store import submit_durable_task

    # Freeze the server's complete effective configuration before acceptance.
    _validate_request_keys(params)
    config = EvaluationConfigV1.model_validate(params["config"])
    if config.backend == "local":
        config = normalize_local_config(config)
    else:
        from quantgpt.research.wq_backend import normalize_wq_config

        config = normalize_wq_config(config)
    params = {**params, "config": config.model_dump(mode="json"), "server_config_frozen": True}
    if params["kind"] == "factor":
        if not 1 <= len(params["definitions"]) <= 100:
            raise ValueError("Batch size must be in [1, 100]")
        for item in params["definitions"]:
            evaluation_hash(FactorDefinitionV1.model_validate(item), config)
        if config.backend == "wq" and len(params["definitions"]) != 1:
            raise ValueError("WQ supports one durable remote evaluation per request")
    elif params["kind"] == "strategy":
        StrategySpecV2.model_validate(params["spec"])
        if config.backend != "local":
            raise ValueError("Strategy research requires the local backend")
    else:
        raise ValueError("Unsupported research request kind")
    payload, created = await submit_durable_task(session, actor_id=str(actor_id), project_id=str(project_id),
        task_type="research_evaluation", params=params, idempotency_key=idempotency_key)
    return {"task_id": payload["task_id"], "status": payload["status"], "created": created,
            "result": payload.get("result"), "poll_url": f"/api/v1/tasks/{payload['task_id']}"}


def run_research_job(payload: dict) -> None:
    """Registered local worker; the durable dispatcher owns lease heartbeats."""
    from quantgpt.db import _get_session_factory
    from quantgpt.task_store import (
        CancelledException,
        check_cancelled,
        persist_task_to_db_async,
        snapshot_task,
        transition_task,
    )

    async def run():
        task_id, actor = payload["task_id"], payload["user_id"]
        attempt = payload["attempt_id"]
        remote = payload.get("params", {}).get("config", {}).get("backend") == "wq"
        try:
            check_cancelled(task_id, expected_attempt=attempt)
            async with _get_session_factory()() as session:
                result = await execute_research_request(session, uuid.UUID(actor), uuid.UUID(payload["project_id"]),
                    payload["params"], cancel_check=lambda: check_cancelled(task_id, expected_attempt=attempt), task_context=payload)
            check_cancelled(task_id, expected_attempt=attempt)
            status = "completed"
            extra = {}
            if remote:
                status = result.get("status", "remote_outcome_unknown")
                if status not in {"completed", "failed", "remote_outcome_unknown", "reconciliation_required",
                                  "local_wait_cancelled", "remote_cancel_confirmed", "cancelled"}:
                    status = "remote_outcome_unknown"
                summary = result.get("summary") or {}
                extra = {"remote_run_ref": summary.get("remote_run_ref"), "retryable": False,
                         "error": summary.get("error"), "progress_message": summary.get("next_action")}
            transition_task(task_id, status, expected_attempt=attempt,
                            result=result, progress=100 if status == "completed" else 0, **extra)
        except CancelledException:
            transition_task(task_id, "local_wait_cancelled" if remote else "cancelled", cancelled=True,
                            expected_attempt=attempt)
        except Exception as exc:
            transition_task(task_id, "failed", error=str(exc), expected_attempt=attempt)
        current = snapshot_task(task_id, expected_attempt=attempt)
        if current:
            await persist_task_to_db_async(task_id, actor, current, strict=True)

    asyncio.run(run())
