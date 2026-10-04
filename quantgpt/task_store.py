"""In-memory task store, rate limiter, and shared helpers for API workers."""

import asyncio
import copy
import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time
import uuid as uuid_mod
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TypeVar, cast, overload

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Report as ReportModel
from .models import Session as SessionModel
from .models import Task as TaskModel

logger = logging.getLogger(__name__)
_T = TypeVar("_T")

TERMINAL_TASK_STATUSES = frozenset({
    "completed", "failed", "cancelled", "iteration_completed", "remote_cancel_confirmed",
    "local_wait_cancelled", "interrupted", "remote_outcome_unknown", "reconciliation_required",
})
PROGRESS_FIELDS = ("progress", "progress_message", "progress_current", "progress_total", "stage")


class TaskConflictError(ValueError):
    """An idempotency key or worker attempt no longer identifies this update."""


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _task_payload(record: TaskModel) -> dict[str, Any]:
    payload = {
        "task_id": record.id, "user_id": str(record.user_id),
        "project_id": str(record.project_id) if record.project_id else None,
        "session_id": str(record.session_id) if record.session_id is not None else None,
        "status": record.status, "task_type": record.task_type,
        "params": record.params, "expression": record.expression,
        "result": record.result, "error": record.error,
        "created_at": _utc(cast(datetime, record.created_at)).timestamp(),
        "updated_at": _utc(cast(datetime, record.updated_at)).timestamp(),
        "revision": record.revision, "_persisted_revision": record.revision,
        "attempt_id": record.attempt_id, "cancelled": record.cancel_requested,
        "cancel_requested": record.cancel_requested, "retryable": record.retryable,
        "dispatch_pending": record.dispatch_pending, "remote_run_ref": record.remote_run_ref,
        "lease_expires_at": _utc(record.lease_expires_at).isoformat() if record.lease_expires_at else None,
    }
    payload.update(record.progress_json or {})
    if record.status in TERMINAL_TASK_STATUSES:
        payload["completed_at"] = payload["updated_at"]
    if str(record.status) == "iteration_completed" and isinstance(record.result, dict):
        payload.update({
            "candidates": record.result.get("candidates", []),
            "candidates_done": len(record.result.get("candidates", [])),
            "candidates_total": len(record.result.get("candidates", [])),
            "search_attempts": record.result.get("search_attempts", []),
            "search_summary": record.result.get("search_summary", {}),
            "task_type": "iteration", "parent_task_id": record.result.get("parent_task_id"),
        })
    return payload


async def authorize_task(session: AsyncSession, record: TaskModel, actor_id: str, *, write: bool = False) -> None:
    """Always recheck current membership, including cache/status/cancel reads."""
    from .research.projects import ProjectAccessError, require_project_member

    if record.project_id:
        await require_project_member(session, actor_id, record.project_id, write=write)
    elif str(record.user_id) != str(actor_id):
        raise ProjectAccessError("Task not found or inaccessible")


async def load_durable_task(session: AsyncSession, task_id: str, actor_id: str, *, write: bool = False) -> dict | None:
    record = await session.get(TaskModel, task_id)
    if record is None:
        return None
    await authorize_task(session, record, actor_id, write=write)
    return _task_payload(record)


async def submit_durable_task(
    session: AsyncSession, *, actor_id: str, task_type: str, params: dict,
    expression: str | None = None, project_id: str | None = None,
    idempotency_key: str | None = None, session_id: str | None = None,
) -> tuple[dict, bool]:
    """Commit the task and pending dispatch flag together before acceptance."""
    from .research.projects import ProjectAccessError, require_project_member

    actor = uuid_mod.UUID(str(actor_id))
    project = uuid_mod.UUID(project_id) if project_id else None
    if project:
        await require_project_member(session, actor, project, write=True)
    if session_id:
        owned_session = await session.scalar(select(SessionModel.id).where(
            SessionModel.id == uuid_mod.UUID(session_id), SessionModel.user_id == actor,
        ))
        if owned_session is None:
            raise ProjectAccessError("Session not found or inaccessible")
    frozen = copy.deepcopy(params)
    encoded = json.dumps({"task_type": task_type, "project_id": project_id, "params": frozen,
                          "expression": expression, "session_id": session_id},
                         sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    request_hash = hashlib.sha256(encoded.encode()).hexdigest()
    key = hashlib.sha256(f"{project_id or ''}:{task_type}:{idempotency_key}".encode()).hexdigest() if idempotency_key else None
    if key:
        existing = (await session.scalars(select(TaskModel).where(TaskModel.user_id == actor,
                                                                 TaskModel.idempotency_key == key))).first()
        if existing:
            if existing.request_hash != request_hash:
                raise TaskConflictError("IDEMPOTENCY_CONFLICT: key was already used with a different payload")
            return _task_payload(existing), False
    record = TaskModel(
        id=uuid_mod.uuid4().hex[:12], user_id=actor, project_id=project,
        session_id=uuid_mod.UUID(session_id) if session_id else None,
        task_type=task_type, status="queued", params=frozen, expression=expression,
        revision=0, idempotency_key=key, request_hash=request_hash,
        cancel_requested=False, dispatch_pending=True, retryable=False,
    )
    try:
        async with session.begin_nested():
            session.add(record)
            await session.flush()
    except IntegrityError:
        if not key:
            raise
        existing = (await session.scalars(select(TaskModel).where(TaskModel.user_id == actor,
                                                                 TaskModel.idempotency_key == key))).first()
        if existing is None or existing.request_hash != request_hash:
            raise TaskConflictError("IDEMPOTENCY_CONFLICT: key was already used with a different payload") from None
        return _task_payload(existing), False
    await session.commit()
    payload = _task_payload(record)
    with tasks_lock:
        tasks[str(record.id)] = payload
    return payload, True


async def claim_durable_task(
    session: AsyncSession, task_id: str, *, lease_seconds: int = 60,
) -> dict | None:
    """Only one worker can claim a queued revision; duplicate dispatch is inert."""
    record = await session.get(TaskModel, task_id)
    if record is None or record.status not in {"queued", "pending"} or record.cancel_requested:
        return None
    await authorize_task(session, record, str(record.user_id), write=True)
    now = datetime.now(timezone.utc)
    attempt = uuid_mod.uuid4().hex
    changed = await session.execute(update(TaskModel).where(
        TaskModel.id == task_id, TaskModel.revision == record.revision,
        TaskModel.status.in_(["queued", "pending"]), TaskModel.cancel_requested.is_(False),
        TaskModel.dispatch_pending.is_(True),
    ).values(status="running", attempt_id=attempt, revision=record.revision + 1,
             lease_expires_at=now + timedelta(seconds=lease_seconds), dispatch_pending=False, updated_at=now))
    await session.commit()
    if cast(CursorResult, changed).rowcount != 1:
        return None
    await session.refresh(record)
    payload = _task_payload(record)
    with tasks_lock:
        tasks[task_id] = payload
    return copy.deepcopy(payload)


async def update_durable_attempt(
    session: AsyncSession, task_id: str, *, attempt_id: str, expected_revision: int,
    status: str = "running", progress: dict | None = None, result: dict | None = None,
    error: str | None = None, lease_seconds: int = 60,
) -> dict | None:
    """Fence completion and heartbeat by lease, attempt, revision and terminal state."""
    if status not in {"running", "completed", "failed"}:
        raise ValueError("Attempt publication supports running, completed, or failed only")
    record = await session.get(TaskModel, task_id)
    if record is None:
        return None
    await authorize_task(session, record, str(record.user_id), write=True)
    now = datetime.now(timezone.utc)
    values: dict[str, Any] = {"status": status, "revision": expected_revision + 1, "updated_at": now}
    if status in TERMINAL_TASK_STATUSES:
        values.update(result=result, error=error, lease_expires_at=None, dispatch_pending=False)
    else:
        values["lease_expires_at"] = now + timedelta(seconds=lease_seconds)
    if progress is not None:
        values["progress_json"] = {key: value for key, value in progress.items() if key in PROGRESS_FIELDS}
    changed = await session.execute(update(TaskModel).where(
        TaskModel.id == task_id, TaskModel.attempt_id == attempt_id,
        TaskModel.revision == expected_revision, TaskModel.cancel_requested.is_(False),
        TaskModel.status.not_in(TERMINAL_TASK_STATUSES), TaskModel.lease_expires_at > now,
    ).values(**values).execution_options(synchronize_session=False))
    await session.commit()
    if cast(CursorResult, changed).rowcount != 1:
        return None
    record = await session.get(TaskModel, task_id, populate_existing=True)
    return _task_payload(record) if record is not None else None


async def cancel_durable_task(session: AsyncSession, task_id: str, actor_id: str) -> dict | None:
    record = await session.get(TaskModel, task_id)
    if record is None:
        return None
    await authorize_task(session, record, actor_id, write=True)
    if record.status not in TERMINAL_TASK_STATUSES:
        remote = (str(record.task_type or "").startswith("wq_") or bool(record.remote_run_ref)
                  or (record.params or {}).get("config", {}).get("backend") == "wq")
        status = "local_wait_cancelled" if remote else "cancelled"
        await session.execute(update(TaskModel).where(
            TaskModel.id == task_id, TaskModel.revision == record.revision,
            TaskModel.status.not_in(TERMINAL_TASK_STATUSES),
        ).values(status=status, cancel_requested=True, revision=record.revision + 1,
                 lease_expires_at=None, dispatch_pending=False, updated_at=datetime.now(timezone.utc)))
        await session.commit()
        await session.refresh(record)
    payload = _task_payload(record)
    with tasks_lock:
        if task_id in tasks:
            tasks[task_id].update(payload)
        else:
            tasks[task_id] = payload
    return payload


async def recover_expired_tasks(session: AsyncSession | None = None) -> int:
    """Mark lost local attempts retryable; never blindly resend a remote POST."""
    if session is None:
        from .db import _get_session_factory
        async with _get_session_factory()() as owned:
            return await recover_expired_tasks(owned)
    now = datetime.now(timezone.utc)
    records = (await session.scalars(select(TaskModel).where(
        TaskModel.status.not_in(TERMINAL_TASK_STATUSES | {"queued", "pending"}),
        (TaskModel.lease_expires_at <= now) | TaskModel.lease_expires_at.is_(None),
    ))).all()
    count = 0
    for record in records:
        remote = (str(record.task_type or "").startswith("wq_") or bool(record.remote_run_ref)
                  or (record.params or {}).get("config", {}).get("backend") == "wq")
        changed = await session.execute(update(TaskModel).where(
            TaskModel.id == record.id, TaskModel.revision == record.revision,
            TaskModel.status.not_in(TERMINAL_TASK_STATUSES),
        ).values(status="remote_outcome_unknown" if remote else "interrupted", retryable=not remote,
                 attempt_id=None, lease_expires_at=None, revision=record.revision + 1,
                 dispatch_pending=False, error="Worker lease expired; prior attempt is fenced", updated_at=now))
        count += cast(CursorResult, changed).rowcount
    await session.commit()
    with tasks_lock:
        for record in records:
            if str(record.id) in tasks:
                tasks[str(record.id)].update(_task_payload(record))
    return count


async def retry_durable_task(session: AsyncSession, task_id: str, actor_id: str) -> dict | None:
    record = await session.get(TaskModel, task_id)
    if record is None:
        return None
    await authorize_task(session, record, actor_id, write=True)
    if str(record.status) != "interrupted" or not record.retryable:
        raise TaskConflictError("TASK_NOT_RETRYABLE: reconcile remote outcomes before any dispatch")
    changed = await session.execute(update(TaskModel).where(
        TaskModel.id == task_id, TaskModel.revision == record.revision, TaskModel.status == "interrupted",
    ).values(status="queued", retryable=False, dispatch_pending=True, attempt_id=None,
             revision=record.revision + 1, error=None, updated_at=datetime.now(timezone.utc)))
    await session.commit()
    if cast(CursorResult, changed).rowcount != 1:
        raise TaskConflictError("TASK_REVISION_CONFLICT")
    await session.refresh(record)
    return _task_payload(record)


async def record_remote_task_reference(session: AsyncSession, task_context: dict | None, remote_ref: str) -> bool:
    """Preserve an accepted remote reference even when local waiting was cancelled.

    This narrow metadata update cannot publish a result or revive a task. A
    different attempt cannot use it to overwrite the current remote identity.
    """
    if task_context is None:
        return True
    task_id, attempt = task_context["task_id"], task_context["attempt_id"]
    changed = await session.execute(update(TaskModel).where(
        TaskModel.id == task_id, TaskModel.attempt_id == attempt,
        TaskModel.user_id == uuid_mod.UUID(task_context["user_id"]),
        TaskModel.status.not_in({"completed", "remote_cancel_confirmed"}),
        TaskModel.remote_run_ref.is_(None) | (TaskModel.remote_run_ref == remote_ref),
    ).values(remote_run_ref=remote_ref, revision=TaskModel.revision + 1,
             updated_at=datetime.now(timezone.utc)).execution_options(synchronize_session=False))
    await session.commit()
    record = await session.get(TaskModel, task_id, populate_existing=True)
    if record is not None:
        with tasks_lock:
            cached = tasks.get(task_id)
            if cached and cached.get("attempt_id") == attempt:
                if str(record.status) in TERMINAL_TASK_STATUSES:
                    cached.update(_task_payload(record))
                else:
                    cached.update(remote_run_ref=record.remote_run_ref, _persisted_revision=record.revision,
                                  revision=max(cached.get("revision", 0), record.revision))
    return cast(CursorResult, changed).rowcount == 1

# ---- Configuration ----

MAX_ACTIVE_TASKS = int(os.environ.get("QUANTGPT_MAX_ACTIVE_TASKS", "100"))
MAX_TOTAL_TASKS = int(os.environ.get("QUANTGPT_MAX_TOTAL_TASKS", "10000"))
TASK_TTL_SECONDS = int(os.environ.get("QUANTGPT_TASK_TTL", "3600"))
TASK_TIMEOUT_SECONDS = int(os.environ.get("QUANTGPT_TASK_TIMEOUT", "600"))
SSE_TIMEOUT_SECONDS = int(os.environ.get("QUANTGPT_SSE_TIMEOUT", "300"))
MAX_SSE_CONNECTIONS = int(os.environ.get("QUANTGPT_MAX_SSE", "1000"))
RATE_LIMIT_PER_MINUTE = int(os.environ.get("QUANTGPT_RATE_LIMIT", "50"))
MAX_PROMPT_LENGTH = int(os.environ.get("QUANTGPT_MAX_PROMPT_LEN", "500"))
MAX_REPORT_FILES = int(os.environ.get("QUANTGPT_MAX_REPORTS", "200"))
MAX_DATE_RANGE_YEARS = 10

# ---- Rate limiter (in-memory, per IP) ----

_rate_buckets: dict[str, list[float]] = defaultdict(list)
_rate_lock = threading.Lock()


def check_rate_limit(ip: str) -> bool:
    now = time.monotonic()
    with _rate_lock:
        bucket = _rate_buckets[ip]
        _rate_buckets[ip] = bucket = [t for t in bucket if now - t < 60]
        if len(bucket) >= RATE_LIMIT_PER_MINUTE:
            return False
        bucket.append(now)
        return True


# ---- Task store (in-memory, bounded) ----

tasks: dict[str, dict] = {}
tasks_lock = threading.Lock()
active_sse_count = 0
sse_lock = threading.Lock()

main_loop: asyncio.AbstractEventLoop | None = None


def active_task_count() -> int:
    return sum(
        1 for t in tasks.values()
        if t.get("status") not in TERMINAL_TASK_STATUSES
    )


class CancelledException(Exception):
    pass


def check_cancelled(task_id: str, *, expected_attempt: str | None = None):
    with tasks_lock:
        task = tasks.get(task_id)
        if expected_attempt is not None and (task is None or task.get("attempt_id") != expected_attempt):
            raise CancelledException("TASK_STALE_ATTEMPT")
        if task and (task.get("cancelled") or task.get("status") in {"interrupted", "failed", "remote_outcome_unknown"}):
            raise CancelledException()


def transition_task(task_id: str, status: str | None = None, *, expected_attempt: str | None = None,
                    **changes: Any) -> dict | None:
    """Publish an atomic in-process state revision without reviving a terminal task."""
    with tasks_lock:
        task = tasks.get(task_id)
        if task is None:
            return None
        if expected_attempt is not None and task.get("attempt_id") != expected_attempt:
            return None
        current = task.get("status")
        if current in TERMINAL_TASK_STATUSES or task.get("cancelled"):
            return dict(task)
        if status:
            task["status"] = status
        if status in {"cancelled", "local_wait_cancelled"}:
            task["cancelled"] = True
            task["cancel_requested"] = True
        task.update(changes)
        task["revision"] = int(task.get("revision", 0)) + 1
        task["updated_at"] = time.time()
        if status in TERMINAL_TASK_STATUSES:
            task["completed_at"] = task["updated_at"]
        return dict(task)


def snapshot_task(task_id: str, *, expected_attempt: str | None = None) -> dict | None:
    """Capture only the calling worker's cache under the same attempt lock."""
    with tasks_lock:
        task = tasks.get(task_id)
        if task is None or (expected_attempt is not None and task.get("attempt_id") != expected_attempt):
            return None
        return copy.deepcopy(task)


def task_result_published(task: dict) -> bool:
    """A completed worker cache is not evidence until its revision is durable."""
    return (task.get("status") not in TERMINAL_TASK_STATUSES or not task.get("attempt_id")
            or int(task.get("_persisted_revision", -1)) >= int(task.get("revision", 0)))


def cleanup_tasks():
    now = time.time()
    with tasks_lock:
        expired = [
            tid for tid, t in tasks.items()
            if now - t.get("created_at", now) > TASK_TTL_SECONDS
            and t.get("status") in TERMINAL_TASK_STATUSES
        ]
        for tid in expired:
            tasks.pop(tid, None)


def cleanup_reports(user_id: str | None = None):
    if user_id:
        report_dir = Path(__file__).resolve().parent.parent / "reports" / user_id
    else:
        report_dir = Path(__file__).resolve().parent.parent / "reports"
    if not report_dir.is_dir():
        return
    files = sorted(report_dir.glob("backtest_report_*.html"), key=lambda f: f.stat().st_mtime)
    if len(files) > MAX_REPORT_FILES:
        for f in files[:len(files) - MAX_REPORT_FILES]:
            try:
                f.unlink()
            except OSError:
                pass


@overload
def sanitize_task_response(task_dict: dict[str, Any]) -> dict[str, Any]: ...


@overload
def sanitize_task_response(task_dict: _T) -> _T: ...


def sanitize_task_response(task_dict: Any) -> Any:
    if not isinstance(task_dict, dict):
        return task_dict
    for key in list(task_dict):
        if key.startswith("_"):
            task_dict.pop(key, None)
    ca = task_dict.get("created_at")
    if isinstance(ca, (int, float)):
        task_dict["created_at"] = datetime.fromtimestamp(ca, tz=timezone.utc).isoformat()
    co = task_dict.get("completed_at")
    if isinstance(co, (int, float)):
        task_dict["completed_at"] = datetime.fromtimestamp(co, tz=timezone.utc).isoformat()
    if "duration_seconds" not in task_dict:
        _ca = task_dict.get("created_at")
        _co = task_dict.get("completed_at")
        if _ca and _co:
            try:
                t0 = datetime.fromisoformat(str(_ca)).timestamp() if isinstance(_ca, str) else float(_ca)
                t1 = datetime.fromisoformat(str(_co)).timestamp() if isinstance(_co, str) else float(_co)
                task_dict["duration_seconds"] = round(t1 - t0, 1)
            except Exception:
                pass
    return task_dict


# ---- DB persistence helpers ----

REPORT_DIR = Path(__file__).resolve().parent.parent / "reports"
SAFE_FILENAME_RE = re.compile(r"^backtest_report_[\w]+\.html$")


async def _persist_task_impl(task_id: str, user_id: str, task_data: dict,
                            report_filename: str | None = None, *, strict: bool = False):
    """Core async implementation of task persistence."""
    from .db import _get_session_factory

    task_data = copy.deepcopy(task_data)
    factory = _get_session_factory()
    async with factory() as session:
        try:
            raw_session_id = task_data.get("session_id")
            session_id = uuid_mod.UUID(raw_session_id) if isinstance(raw_session_id, str) else raw_session_id
            real_created = task_data.get("created_at")
            real_completed = task_data.get("completed_at")
            ts_created = None
            ts_completed = None
            if isinstance(real_created, (int, float)):
                ts_created = datetime.fromtimestamp(real_created, tz=timezone.utc)
            if isinstance(real_completed, (int, float)):
                ts_completed = datetime.fromtimestamp(real_completed, tz=timezone.utc)

            now = datetime.now(timezone.utc)
            existing = await session.get(TaskModel, task_id)
            if existing is not None:
                await authorize_task(session, existing, user_id, write=True)
                if str(existing.user_id) != str(user_id):
                    raise PermissionError("Task actor is immutable")
                if existing.status in TERMINAL_TASK_STATUSES or existing.cancel_requested:
                    with tasks_lock:
                        if task_id in tasks:
                            tasks[task_id].update(_task_payload(existing))
                    return
                if (existing.attempt_id or task_data.get("attempt_id")) and (
                    existing.attempt_id != task_data.get("attempt_id")
                    or existing.revision != task_data.get("_persisted_revision", existing.revision)
                    or existing.lease_expires_at is None or _utc(existing.lease_expires_at) <= now
                ):
                    raise TaskConflictError("TASK_STALE_ATTEMPT: stale worker cannot publish")
            revision = max(int(task_data.get("revision", 0)), int(existing.revision) + 1 if existing else 0)
            status = task_data.get("status", "failed")
            values: dict[str, Any] = {
                "status": status, "task_type": task_data.get("task_type", "backtest"),
                "params": task_data.get("params"), "expression": task_data.get("expression"),
                "result": task_data.get("result"), "error": task_data.get("error"),
                "revision": revision, "cancel_requested": bool(task_data.get("cancelled")),
                "progress_json": {key: task_data[key] for key in PROGRESS_FIELDS if key in task_data},
                "updated_at": ts_completed or now,
                "dispatch_pending": False,
                "remote_run_ref": task_data.get("remote_run_ref") or (existing.remote_run_ref if existing else None),
                "retryable": bool(task_data.get("retryable", False)),
            }
            if status in TERMINAL_TASK_STATUSES:
                values["lease_expires_at"] = None
            elif task_data.get("attempt_id"):
                values["lease_expires_at"] = now + timedelta(seconds=60)
            if existing is None:
                values.update(id=task_id, user_id=uuid_mod.UUID(str(user_id)), session_id=session_id,
                              project_id=uuid_mod.UUID(task_data["project_id"]) if task_data.get("project_id") else None,
                              attempt_id=task_data.get("attempt_id"))
            if ts_created:
                values["created_at"] = ts_created
            if existing is None:
                session.add(TaskModel(**values))
            else:
                guards = [
                    TaskModel.id == task_id, TaskModel.revision == existing.revision,
                    TaskModel.status.not_in(TERMINAL_TASK_STATUSES), TaskModel.cancel_requested.is_(False),
                ]
                if existing.attempt_id or task_data.get("attempt_id"):
                    guards.extend([TaskModel.attempt_id == task_data.get("attempt_id"),
                                   TaskModel.lease_expires_at > datetime.now(timezone.utc)])
                changed = await session.execute(update(TaskModel).where(*guards).values(**values)
                                                .execution_options(synchronize_session=False))
                if cast(CursorResult, changed).rowcount != 1:
                    raise TaskConflictError("TASK_REVISION_CONFLICT: task changed before publication")

            if report_filename and not await session.scalar(select(ReportModel.id).where(
                ReportModel.task_id == task_id, ReportModel.filename == report_filename,
            )):
                report_record = ReportModel(
                    user_id=uuid_mod.UUID(user_id) if isinstance(user_id, str) else user_id,
                    task_id=task_id,
                    filename=report_filename,
                )
                session.add(report_record)

            if session_id:
                result = await session.execute(
                    select(SessionModel).where(SessionModel.id == session_id)
                )
                sess_record = result.scalar_one_or_none()
                if sess_record is not None and not cast(str | None, sess_record.name):
                    prompt = (task_data.get("params") or {}).get("prompt", "")
                    if prompt:
                        sess_record.name = prompt[:30]

            await session.commit()
            with tasks_lock:
                cached = tasks.get(task_id)
                if cached and cached.get("attempt_id") == task_data.get("attempt_id"):
                    cached["_persisted_revision"] = revision
                    cached["revision"] = max(cached.get("revision", 0), revision)
            logger.info(f"[{task_id}] persisted to DB")
        except Exception as e:
            await session.rollback()
            if isinstance(e, TaskConflictError):
                authoritative = await session.get(TaskModel, task_id, populate_existing=True)
                if authoritative is not None:
                    with tasks_lock:
                        cached = tasks.get(task_id)
                        if cached and cached.get("attempt_id") == task_data.get("attempt_id"):
                            cached.update(_task_payload(authoritative))
            logger.error(f"[{task_id}] DB persist failed: {e}")
            if strict:
                raise


async def persist_task_to_db_async(task_id: str, user_id: str, task_data: dict,
                                   report_filename: str | None = None, *, strict: bool = False):
    """Async version — use from async (MCP) context. Never deadlocks."""
    await _persist_task_impl(task_id, user_id, task_data, report_filename, strict=strict)


def persist_task_to_db(task_id: str, user_id: str, task_data: dict, report_filename: str | None = None):
    """Sync wrapper — use from sync (HTTP route background) context only."""
    if main_loop and main_loop.is_running():
        future = asyncio.run_coroutine_threadsafe(
            _persist_task_impl(task_id, user_id, task_data, report_filename), main_loop
        )
        try:
            future.result(timeout=30)
        except Exception as e:
            logger.error(f"[{task_id}] DB persist error: {e}")
    else:
        def _run():
            try:
                asyncio.run(_persist_task_impl(task_id, user_id, task_data, report_filename))
            except Exception as e:
                logger.error(f"[{task_id}] DB persist thread error: {e}")
        t = threading.Thread(target=_run, daemon=True)
        t.start()
        t.join(timeout=30)


# ---- SSE ticket store (short-lived, single-use) ----

_sse_tickets: dict[str, dict] = {}
_sse_tickets_lock = threading.Lock()


def create_sse_ticket(task_id: str, user_id: str) -> str:
    """Generate a short-lived, single-use ticket for SSE authentication.

    The ticket expires after 60 seconds and is consumed on first validation.
    """
    ticket = secrets.token_urlsafe()
    with _sse_tickets_lock:
        # Opportunistic cleanup of expired tickets
        now = time.monotonic()
        expired = [k for k, v in _sse_tickets.items() if v["expires"] < now]
        for k in expired:
            _sse_tickets.pop(k, None)

        _sse_tickets[ticket] = {
            "task_id": task_id,
            "user_id": user_id,
            "expires": now + 60,
        }
    return ticket


def validate_sse_ticket(ticket: str, task_id: str) -> str | None:
    """Validate and consume an SSE ticket.

    Returns the user_id if valid, or None if the ticket is invalid, expired,
    or does not match the requested task_id.
    """
    with _sse_tickets_lock:
        entry = _sse_tickets.pop(ticket, None)
    if entry is None:
        return None
    if entry["task_id"] != task_id:
        return None
    if time.monotonic() > entry["expires"]:
        return None
    return entry["user_id"]


# ---- Report ticket store (short-lived, single-use) ----

_report_tickets: dict[str, dict] = {}
_report_tickets_lock = threading.Lock()


def create_report_ticket(filename: str, user_id: str) -> str:
    """Generate a short-lived, single-use ticket for report downloads."""
    ticket = secrets.token_urlsafe()
    with _report_tickets_lock:
        now = time.monotonic()
        expired = [k for k, v in _report_tickets.items() if v["expires"] < now]
        for k in expired:
            _report_tickets.pop(k, None)

        _report_tickets[ticket] = {
            "filename": filename,
            "user_id": user_id,
            "expires": now + 60,
        }
    return ticket


def validate_report_ticket(ticket: str, filename: str) -> str | None:
    """Validate and consume a report download ticket."""
    with _report_tickets_lock:
        entry = _report_tickets.pop(ticket, None)
    if entry is None:
        return None
    if entry["filename"] != filename:
        return None
    if time.monotonic() > entry["expires"]:
        return None
    return entry["user_id"]


def persist_report_to_db(task_id: str, user_id: str, report_filename: str):
    from .db import _get_session_factory

    async def _do():
        factory = _get_session_factory()
        async with factory() as session:
            try:
                report_record = ReportModel(
                    user_id=uuid_mod.UUID(user_id) if isinstance(user_id, str) else user_id,
                    task_id=task_id,
                    filename=report_filename,
                )
                session.add(report_record)
                await session.commit()
            except Exception as e:
                await session.rollback()
                logger.error(f"Report persist failed: {e}")

    if main_loop and main_loop.is_running():
        future = asyncio.run_coroutine_threadsafe(_do(), main_loop)
        try:
            future.result(timeout=30)
        except Exception as e:
            logger.error(f"Report persist error: {e}")
    else:
        logger.error("main event loop not available for report persist")
