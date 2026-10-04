"""Shared task lifecycle helpers for MCP tools.

Ensures MCP tools create Task records identical to HTTP API routes,
so all tasks appear in the same list with consistent format.
Task records are written to DB at creation (running) AND completion,
so server restarts never lose data.

All functions are async — MCP tools are async, so we await DB writes
directly instead of using run_coroutine_threadsafe (which deadlocks
the event loop when called from async context).
"""

import asyncio
import logging
import time
import uuid as uuid_mod
from contextvars import ContextVar, Token
from typing import Any

from .research.projects import resolve_mcp_actor
from .task_store import (
    TERMINAL_TASK_STATUSES,
    cancel_durable_task,
    claim_durable_task,
    load_durable_task,
    persist_task_to_db_async,
    sanitize_task_response,
    snapshot_task,
    submit_durable_task,
    tasks,
    tasks_lock,
    transition_task,
)

logger = logging.getLogger(__name__)

_FORCED_MCP_TASK_ID: ContextVar[str | None] = ContextVar("quantgpt_forced_mcp_task_id", default=None)
_MCP_ATTEMPTS: ContextVar[dict[str, str] | None] = ContextVar("quantgpt_mcp_attempts", default=None)
_HEARTBEATS: dict[str, asyncio.Task] = {}
_HEARTBEAT_ATTEMPTS: dict[str, str] = {}


def _track_attempt(task: dict) -> None:
    attempt_id = task.get("attempt_id")
    if not attempt_id:
        return
    task_id = task["task_id"]
    _MCP_ATTEMPTS.set({**(_MCP_ATTEMPTS.get() or {}), task_id: attempt_id})
    if task_id in _HEARTBEATS and _HEARTBEAT_ATTEMPTS.get(task_id) == attempt_id:
        return
    _stop_heartbeat(task_id)

    async def heartbeat():
        from .task_store import TASK_TIMEOUT_SECONDS
        deadline = time.monotonic() + TASK_TIMEOUT_SECONDS
        while True:
            await asyncio.sleep(10)
            current = snapshot_task(task_id, expected_attempt=attempt_id)
            if not current or current.get("status") in TERMINAL_TASK_STATUSES:
                return
            if time.monotonic() >= deadline:
                remote = str(current.get("task_type", "")).startswith("wq_")
                current = transition_task(task_id, "remote_outcome_unknown" if remote else "failed", cancelled=True,
                                          expected_attempt=attempt_id,
                                          error="TASK_DEADLINE_EXCEEDED: reconcile any remote outcome before retrying")
                if current is None:
                    return
            await persist_task_to_db_async(task_id, current["user_id"], current)

    future = asyncio.create_task(heartbeat())
    _HEARTBEATS[task_id] = future
    _HEARTBEAT_ATTEMPTS[task_id] = attempt_id
    future.add_done_callback(lambda done: _HEARTBEATS.pop(task_id, None) if _HEARTBEATS.get(task_id) is done else None)


def _stop_heartbeat(task_id: str, *, expected_attempt: str | None = None) -> None:
    if expected_attempt is not None and _HEARTBEAT_ATTEMPTS.get(task_id) != expected_attempt:
        return
    future = _HEARTBEATS.pop(task_id, None)
    _HEARTBEAT_ATTEMPTS.pop(task_id, None)
    if future is not None:
        future.cancel()


def force_mcp_task_id(task_id: str) -> Token[str | None]:
    return _FORCED_MCP_TASK_ID.set(task_id)


def reset_forced_mcp_task_id(token: Token[str | None]) -> None:
    _FORCED_MCP_TASK_ID.reset(token)


async def start_mcp_task(task_type: str, expression: str | None, params: dict, *,
                         project_id: str | None = None, idempotency_key: str | None = None) -> str:
    actor_id = str(resolve_mcp_actor())
    params = {**params, "source": "mcp"}
    forced_task_id = _FORCED_MCP_TASK_ID.get()
    if forced_task_id:
        with tasks_lock:
            task = tasks.get(forced_task_id)
            if task is not None:
                if task.get("user_id") != actor_id:
                    raise PermissionError("Task not found or inaccessible")
                if task.get("status") in TERMINAL_TASK_STATUSES:
                    return forced_task_id
                task["task_type"] = task_type
                task["params"] = params
                task["expression"] = expression
                if not task.get("cancelled"):
                    task["status"] = "running"
                task["updated_at"] = time.time()
                _track_attempt(task)
                return forced_task_id

    if project_id or idempotency_key:
        from .db import _get_session_factory
        async with _get_session_factory()() as session:
            task, created = await submit_durable_task(
                session, actor_id=actor_id, task_type=task_type, params=params,
                expression=expression, project_id=project_id, idempotency_key=idempotency_key,
            )
            if created:
                await claim_durable_task(session, task["task_id"])
            _track_attempt(tasks.get(task["task_id"], task))
            return task["task_id"]

    task_id = forced_task_id or uuid_mod.uuid4().hex[:12]
    task = {
        "task_id": task_id,
        "user_id": actor_id,
        "session_id": None,
        "status": "running",
        "task_type": task_type,
        "cancelled": False,
        "params": params,
        "expression": expression,
        "created_at": time.time(),
        "attempt_id": uuid_mod.uuid4().hex,
        "revision": 0,
    }
    with tasks_lock:
        tasks[task_id] = task

    try:
        await persist_task_to_db_async(task_id, actor_id, task, strict=True)
    except Exception as e:
        logger.error(f"[{task_id}] MCP task initial persist error: {e}")
        with tasks_lock:
            tasks.pop(task_id, None)
        raise

    _track_attempt(task)
    return task_id


def update_mcp_task_progress_sync(
    task_id: str,
    *,
    status: str | None = None,
    progress: int | float | None = None,
    progress_message: str | None = None,
    progress_current: int | None = None,
    progress_total: int | None = None,
    stage: str | None = None,
) -> dict[str, Any] | None:
    """Update in-memory MCP task progress from sync code or worker threads."""
    attempt = (_MCP_ATTEMPTS.get() or {}).get(task_id)
    current = tasks.get(task_id)
    if attempt and current and current.get("attempt_id") != attempt:
        return None
    changes = {}
    if progress is not None:
        changes["progress"] = max(0, min(100, int(progress)))
    if progress_message is not None:
        changes["progress_message"] = progress_message
    if progress_current is not None:
        changes["progress_current"] = max(0, int(progress_current))
    if progress_total is not None:
        changes["progress_total"] = max(0, int(progress_total))
    if stage is not None:
        changes["stage"] = stage
    return transition_task(task_id, status, expected_attempt=attempt, **changes)


async def update_mcp_task_progress(
    task_id: str,
    *,
    status: str | None = None,
    progress: int | float | None = None,
    progress_message: str | None = None,
    progress_current: int | None = None,
    progress_total: int | None = None,
    stage: str | None = None,
    persist: bool = True,
) -> dict[str, Any] | None:
    """Update MCP task progress and optionally persist it to the shared task table."""
    task = update_mcp_task_progress_sync(
        task_id,
        status=status,
        progress=progress,
        progress_message=progress_message,
        progress_current=progress_current,
        progress_total=progress_total,
        stage=stage,
    )
    if task and persist:
        try:
            await persist_task_to_db_async(task_id, task["user_id"], task)
        except Exception as e:
            logger.error(f"[{task_id}] MCP task progress persist error: {e}")
    return task


async def request_mcp_task_cancel(task_id: str) -> dict[str, Any]:
    """Request cooperative cancellation for an MCP task."""
    actor_id = str(resolve_mcp_actor())
    cached = tasks.get(task_id)
    if cached is None or cached.get("project_id"):
        from .db import _get_session_factory
        async with _get_session_factory()() as session:
            payload = await cancel_durable_task(session, task_id, actor_id)
            return sanitize_task_response(payload) if payload else {"error_code": "MCP_TASK_NOT_FOUND", "task_id": task_id}
    if cached.get("user_id") != actor_id:
        return {"error_code": "MCP_TASK_NOT_FOUND", "task_id": task_id}
    if cached.get("status") in TERMINAL_TASK_STATUSES:
        return sanitize_task_response(dict(cached))
    _stop_heartbeat(task_id)
    remote = str(cached.get("task_type", "")).startswith("wq_") or bool(cached.get("remote_run_ref"))
    task = update_mcp_task_progress_sync(
        task_id,
        status="local_wait_cancelled" if remote else "cancelled",
        progress_message="cancellation requested",
        stage="cancelled",
    )
    if task is None:
        return {"error_code": "MCP_TASK_NOT_FOUND", "task_id": task_id}
    with tasks_lock:
        stored = tasks.get(task_id)
        if stored is not None:
            stored["cancelled"] = True
            stored.setdefault("completed_at", time.time())
            task = dict(stored)
    try:
        await persist_task_to_db_async(task_id, actor_id, task)
    except Exception as e:
        logger.error(f"[{task_id}] MCP task cancel persist error: {e}")
    return sanitize_task_response(dict(task))


def get_mcp_task_status_payload(task_id: str, *, include_result: bool = False) -> dict[str, Any]:
    """Return a sanitized task status payload for MCP polling."""
    with tasks_lock:
        task = tasks.get(task_id)
        if task is None or task.get("user_id") != str(resolve_mcp_actor()) or task.get("project_id"):
            return {"error_code": "MCP_TASK_NOT_FOUND", "task_id": task_id}
        payload = dict(task)
    if not include_result:
        payload.pop("result", None)
    return sanitize_task_response(payload)


async def get_mcp_task_status_payload_async(task_id: str, *, include_result: bool = False) -> dict[str, Any]:
    """Recover persisted status/results and reauthorize current project access."""
    from .db import _get_session_factory
    from .research.projects import ProjectAccessError

    actor_id = str(resolve_mcp_actor())
    try:
        async with _get_session_factory()() as session:
            persisted = await load_durable_task(session, task_id, actor_id)
    except ProjectAccessError:
        return {"error_code": "MCP_TASK_NOT_FOUND", "task_id": task_id}
    if persisted is None:
        return get_mcp_task_status_payload(task_id, include_result=include_result)
    with tasks_lock:
        cached = tasks.get(task_id)
        if (cached and cached.get("attempt_id") == persisted.get("attempt_id")
                and persisted["status"] not in TERMINAL_TASK_STATUSES
                and cached.get("status") not in TERMINAL_TASK_STATUSES):
            payload = dict(cached)
        else:
            payload = persisted
    if not include_result:
        payload.pop("result", None)
    return sanitize_task_response(payload)


async def complete_mcp_task(
    task_id: str,
    result: dict | None = None,
    error: str | None = None,
    expression: str | None = None,
):
    actor_id = str(resolve_mcp_actor())
    task = tasks.get(task_id)
    original_attempt = (_MCP_ATTEMPTS.get() or {}).get(task_id)
    if not task:
        from .db import _get_session_factory
        async with _get_session_factory()() as session:
            task = await load_durable_task(session, task_id, actor_id, write=True)
        if task is None:
            raise ValueError("MCP_TASK_NOT_FOUND: completion requires the original frozen task")
        if not original_attempt or task.get("attempt_id") != original_attempt:
            raise ValueError("TASK_STALE_ATTEMPT: completion after eviction requires the original attempt")
        with tasks_lock:
            current = tasks.get(task_id)
            if current is not None and current.get("attempt_id") != original_attempt:
                raise ValueError("TASK_STALE_ATTEMPT: cache was replaced during completion lookup")
            if current is None:
                tasks[task_id] = task
            else:
                task = current
    if task.get("user_id") != actor_id:
        raise PermissionError("Task not found or inaccessible")
    if task.get("attempt_id") and not original_attempt:
        raise ValueError("TASK_STALE_ATTEMPT: completion requires the originating worker context")
    if original_attempt and task.get("attempt_id") != original_attempt:
        raise ValueError("TASK_STALE_ATTEMPT: old worker cannot complete a newer attempt")
    _stop_heartbeat(task_id, expected_attempt=original_attempt)
    if task.get("cancelled"):
        if result and result.get("error_code") == "MCP_TASK_CANCELLED":
            task["result"] = result
    else:
        changes = {"result": result, "error": error}
        if expression:
            changes["expression"] = expression
        task = transition_task(task_id, "failed" if error else "completed", expected_attempt=original_attempt, **changes)
        if task is None:
            raise ValueError("TASK_STALE_ATTEMPT: old worker cannot complete a newer attempt")

    try:
        snapshot = snapshot_task(task_id, expected_attempt=original_attempt)
        if snapshot is not None:
            await persist_task_to_db_async(task_id, actor_id, snapshot)
    except Exception as e:
        logger.error(f"[{task_id}] MCP task persist error: {e}")
