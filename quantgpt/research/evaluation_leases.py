"""Database ownership of local numerical work, shared across task/stdio processes."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import cast

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from quantgpt.models import EvaluationExecutionLease, Experiment, Task

LEASE_SECONDS = 60


class ExecutionLeaseLost(RuntimeError):
    """A newer execution owns this evaluation and may publish its result."""


def retryable_local_conflict(exc: BaseException) -> bool:
    if isinstance(exc, ExecutionLeaseLost):
        return True
    if not isinstance(exc, DBAPIError):
        return False
    original = exc.orig
    # SQLITE_BUSY / SQLITE_LOCKED include extended codes such as BUSY_SNAPSHOT.
    code = getattr(original, "sqlite_errorcode", 0)
    if exc.connection_invalidated or (isinstance(code, int) and (code & 255) in {5, 6}):
        return True
    state = getattr(original, "sqlstate", "") or ""
    return state.startswith("08") or state in {"40001", "40P01", "55P03"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _expiry() -> datetime:
    return _now() + timedelta(seconds=LEASE_SECONDS)


async def _task_still_active(session: AsyncSession, task_context: dict) -> bool:
    task_id = await session.scalar(select(Task.id).where(Task.id == task_context["task_id"],
        Task.attempt_id == task_context["attempt_id"], Task.status == "running", Task.cancel_requested.is_(False),
        Task.lease_expires_at > _now()))
    return task_id is not None


async def fence_evaluation_execution(session: AsyncSession, evaluation_id: str, owner_token: str) -> None:
    """Lock an unexpired ownership row in the transaction that writes results."""
    result = await session.execute(update(EvaluationExecutionLease).where(
        EvaluationExecutionLease.evaluation_id == evaluation_id, EvaluationExecutionLease.owner_token == owner_token,
        EvaluationExecutionLease.expires_at > _now(),
    ).values(expires_at=_expiry()))
    if cast(CursorResult, result).rowcount != 1:
        raise ExecutionLeaseLost("EVALUATION_EXECUTION_LEASE_LOST")


@asynccontextmanager
async def evaluation_execution(session: AsyncSession, evaluation_id: str, *,
                               cancel_check: Callable[[], None] | None = None,
                               task_context: dict | None = None) -> AsyncIterator[str | None]:
    """Wait for a saved result or claim its computation after a prior owner exits.

    Registration must have committed. The publisher fences ownership immediately
    before writing its result and commits it before leaving this context.
    """
    if session.bind is None:
        raise ValueError("A bound research session is required")
    factory = async_sessionmaker(session.bind, expire_on_commit=False)
    dialect = session.get_bind().dialect.name
    insert = sqlite_insert if dialect == "sqlite" else pg_insert
    token = uuid.uuid4().hex
    delay = 0.1
    while True:
        if cancel_check:
            cancel_check()
        if task_context is not None and not await _task_still_active(session, task_context):
            from quantgpt.task_store import CancelledException

            raise CancelledException("TASK_ATTEMPT_NO_LONGER_AUTHORIZED")
        summary, expires = (await session.execute(select(Experiment.result_summary, EvaluationExecutionLease.expires_at)
            .outerjoin(EvaluationExecutionLease, EvaluationExecutionLease.evaluation_id == Experiment.experiment_id)
            .where(Experiment.experiment_id == evaluation_id))).one()
        if summary is not None:
            await session.commit()
            yield None
            return
        if expires is not None and expires.replace(tzinfo=expires.tzinfo or timezone.utc) > _now():
            # Followers only read while a lease is live. In SQLite even an
            # upsert that changes no rows would otherwise contend for a write.
            await session.commit()
            await asyncio.sleep(delay)
            delay = min(1.0, delay * 2)
            continue
        lease = insert(EvaluationExecutionLease).values(evaluation_id=evaluation_id, owner_token=token,
                                                         expires_at=_expiry())
        result = await session.execute(lease.on_conflict_do_update(
            index_elements=[EvaluationExecutionLease.evaluation_id],
            set_={"owner_token": token, "expires_at": _expiry()}, where=EvaluationExecutionLease.expires_at <= _now(),
        ))
        acquired = cast(CursorResult, result).rowcount == 1
        # The previous owner may have published since our first read.
        if acquired:
            summary = await session.scalar(select(Experiment.result_summary).where(Experiment.experiment_id == evaluation_id))
            if summary is not None:
                await session.execute(delete(EvaluationExecutionLease).where(
                    EvaluationExecutionLease.evaluation_id == evaluation_id, EvaluationExecutionLease.owner_token == token,
                ))
        await session.commit()
        if summary is not None:
            yield None
            return
        if acquired:
            break

    async def renew():
        while True:
            await asyncio.sleep(LEASE_SECONDS / 3)
            async with factory() as heartbeat:
                if task_context is not None and not await _task_still_active(heartbeat, task_context):
                    return
                await fence_evaluation_execution(heartbeat, evaluation_id, token)
                await heartbeat.commit()

    heartbeat = asyncio.create_task(renew())
    try:
        yield token
    except BaseException:
        await session.rollback()
        raise
    finally:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
        async with factory() as cleanup:
            await cleanup.execute(delete(EvaluationExecutionLease).where(
                EvaluationExecutionLease.evaluation_id == evaluation_id, EvaluationExecutionLease.owner_token == token,
            ))
            await cleanup.commit()
