"""Shared task lifecycle helpers for MCP tools.

Ensures MCP tools create Task records identical to HTTP API routes,
so all tasks appear in the same list with consistent format.
Task records are written to DB at creation (running) AND completion,
so server restarts never lose data.

All functions are async — MCP tools are async, so we await DB writes
directly instead of using run_coroutine_threadsafe (which deadlocks
the event loop when called from async context).
"""

import logging
import time
import uuid as uuid_mod
from contextvars import ContextVar, Token
from typing import Any

from .auth import _DEV_USER_ID
from .task_store import persist_task_to_db_async, sanitize_task_response, tasks, tasks_lock

logger = logging.getLogger(__name__)

_DEV_USER_ID_STR = str(_DEV_USER_ID)
_FORCED_MCP_TASK_ID: ContextVar[str | None] = ContextVar("quantgpt_forced_mcp_task_id", default=None)


def force_mcp_task_id(task_id: str) -> Token[str | None]:
    return _FORCED_MCP_TASK_ID.set(task_id)


def reset_forced_mcp_task_id(token: Token[str | None]) -> None:
    _FORCED_MCP_TASK_ID.reset(token)


async def start_mcp_task(task_type: str, expression: str | None, params: dict) -> str:
    params = {**params, "source": "mcp"}
    forced_task_id = _FORCED_MCP_TASK_ID.get()
    if forced_task_id:
        with tasks_lock:
            task = tasks.get(forced_task_id)
            if task is not None:
                task["task_type"] = task_type
                task["params"] = params
                task["expression"] = expression
                if not task.get("cancelled"):
                    task["status"] = "running"
                task["updated_at"] = time.time()
                return forced_task_id

    task_id = forced_task_id or uuid_mod.uuid4().hex[:12]
    task = {
        "task_id": task_id,
        "user_id": _DEV_USER_ID_STR,
        "session_id": None,
        "status": "running",
        "task_type": task_type,
        "cancelled": False,
        "params": params,
        "expression": expression,
        "created_at": time.time(),
    }
    with tasks_lock:
        tasks[task_id] = task

    try:
        await persist_task_to_db_async(task_id, _DEV_USER_ID_STR, task)
    except Exception as e:
        logger.error(f"[{task_id}] MCP task initial persist error: {e}")

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
    with tasks_lock:
        task = tasks.get(task_id)
        if task is None:
            return None
        if task.get("cancelled") and status != "cancelled":
            return dict(task)
        if status:
            task["status"] = status
            if status == "cancelled":
                task["cancelled"] = True
        if progress is not None:
            task["progress"] = max(0, min(100, int(progress)))
        if progress_message is not None:
            task["progress_message"] = progress_message
        if progress_current is not None:
            task["progress_current"] = max(0, int(progress_current))
        if progress_total is not None:
            task["progress_total"] = max(0, int(progress_total))
        if stage is not None:
            task["stage"] = stage
        task["updated_at"] = time.time()
        return dict(task)


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
            await persist_task_to_db_async(task_id, _DEV_USER_ID_STR, task)
        except Exception as e:
            logger.error(f"[{task_id}] MCP task progress persist error: {e}")
    return task


async def request_mcp_task_cancel(task_id: str) -> dict[str, Any]:
    """Request cooperative cancellation for an MCP task."""
    task = update_mcp_task_progress_sync(
        task_id,
        status="cancelled",
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
        await persist_task_to_db_async(task_id, _DEV_USER_ID_STR, task)
    except Exception as e:
        logger.error(f"[{task_id}] MCP task cancel persist error: {e}")
    return sanitize_task_response(dict(task))


def get_mcp_task_status_payload(task_id: str, *, include_result: bool = False) -> dict[str, Any]:
    """Return a sanitized task status payload for MCP polling."""
    with tasks_lock:
        task = tasks.get(task_id)
        if task is None:
            return {"error_code": "MCP_TASK_NOT_FOUND", "task_id": task_id}
        payload = dict(task)
    if not include_result:
        payload.pop("result", None)
    return sanitize_task_response(payload)


async def complete_mcp_task(
    task_id: str,
    result: dict | None = None,
    error: str | None = None,
    expression: str | None = None,
):
    task = tasks.get(task_id)
    if not task:
        logger.warning(f"[{task_id}] task evicted from memory, reconstructing for DB persist")
        status = "failed" if error else "completed"
        if result and result.get("error_code") == "MCP_TASK_CANCELLED":
            status = "cancelled"
        task = {
            "task_id": task_id,
            "user_id": _DEV_USER_ID_STR,
            "session_id": None,
            "status": status,
            "task_type": "mcp_unknown",
            "cancelled": status == "cancelled",
            "params": {"source": "mcp", "note": "reconstructed after memory eviction"},
            "expression": expression,
            "created_at": time.time(),
            "completed_at": time.time(),
        }
        if error:
            task["error"] = error
        if result:
            task["result"] = result
        with tasks_lock:
            tasks[task_id] = task
    else:
        task["completed_at"] = time.time()
        if task.get("cancelled") or task.get("status") == "cancelled":
            task["status"] = "cancelled"
            task["cancelled"] = True
            if error:
                task["error"] = error
        elif error:
            task["status"] = "failed"
            task["error"] = error
        else:
            task["status"] = "completed"

        if expression:
            task["expression"] = expression
        if result:
            task["result"] = result

    try:
        await persist_task_to_db_async(task_id, _DEV_USER_ID_STR, task)
    except Exception as e:
        logger.error(f"[{task_id}] MCP task persist error: {e}")
