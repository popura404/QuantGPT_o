"""Fault injection at the durable acceptance/claim/cancel/publication boundaries."""

import asyncio
import json
import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker
from starlette.requests import Request

from quantgpt.models import ProjectMember, Task
from quantgpt.research.projects import ProjectAccessError, create_project
from quantgpt.task_store import (
    TaskConflictError,
    cancel_durable_task,
    claim_durable_task,
    load_durable_task,
    recover_expired_tasks,
    retry_durable_task,
    submit_durable_task,
    tasks,
    transition_task,
    update_durable_attempt,
)


@pytest.fixture(autouse=True)
def _isolated_task_cache():
    tasks.clear()
    yield
    tasks.clear()


async def _submit(db_session, user, **kwargs):
    return await submit_durable_task(db_session, actor_id=str(user.id), task_type="backtest",
                                     params={"expression": "close"}, **kwargs)


@pytest.mark.asyncio
async def test_acceptance_survives_memory_loss_before_dispatch(db_session, test_user):
    accepted, created = await _submit(db_session, test_user)
    tasks.clear()  # crash after commit, before the worker is launched
    recovered = await load_durable_task(db_session, accepted["task_id"], str(test_user.id))
    assert created
    assert recovered["status"] == "queued"
    assert recovered["dispatch_pending"] is True
    assert recovered["params"] == {"expression": "close"}


@pytest.mark.asyncio
async def test_idempotency_same_payload_reuses_and_different_payload_conflicts(db_session, test_user):
    first, created = await _submit(db_session, test_user, idempotency_key="attempt-1")
    again, created_again = await _submit(db_session, test_user, idempotency_key="attempt-1")
    assert created and not created_again
    assert again["task_id"] == first["task_id"]
    with pytest.raises(TaskConflictError, match="IDEMPOTENCY_CONFLICT"):
        await submit_durable_task(db_session, actor_id=str(test_user.id), task_type="backtest",
                                  params={"expression": "volume"}, idempotency_key="attempt-1")
    assert len((await db_session.scalars(select(Task))).all()) == 1


@pytest.mark.asyncio
async def test_duplicate_dispatch_cannot_claim_twice(db_session, test_user):
    task, _ = await _submit(db_session, test_user)
    first = await claim_durable_task(db_session, task["task_id"])
    duplicate = await claim_durable_task(db_session, task["task_id"])
    assert first["attempt_id"]
    assert duplicate is None


@pytest.mark.asyncio
async def test_cancel_wins_over_late_completion_and_does_not_store_results(db_session, test_user):
    task, _ = await _submit(db_session, test_user)
    claimed = await claim_durable_task(db_session, task["task_id"])
    cancelled = await cancel_durable_task(db_session, task["task_id"], str(test_user.id))
    result = await update_durable_attempt(db_session, task["task_id"], attempt_id=claimed["attempt_id"],
                                          expected_revision=claimed["revision"], status="completed",
                                          result={"invalid_late_result": True})
    assert cancelled["status"] == "cancelled"
    assert result is None
    final = await load_durable_task(db_session, task["task_id"], str(test_user.id))
    assert final["status"] == "cancelled"
    assert final["result"] is None


@pytest.mark.asyncio
async def test_completed_task_is_immutable_under_cancel(db_session, test_user):
    task, _ = await _submit(db_session, test_user)
    claimed = await claim_durable_task(db_session, task["task_id"])
    finished = await update_durable_attempt(db_session, task["task_id"], attempt_id=claimed["attempt_id"],
                                            expected_revision=claimed["revision"], status="completed", result={"ok": 1})
    cancelled = await cancel_durable_task(db_session, task["task_id"], str(test_user.id))
    assert cancelled["status"] == "completed"
    assert cancelled["result"] == {"ok": 1}
    assert cancelled["revision"] == finished["revision"]


@pytest.mark.asyncio
async def test_expired_attempt_is_fenced_after_explicit_retry(db_session, test_user):
    task, _ = await _submit(db_session, test_user)
    first = await claim_durable_task(db_session, task["task_id"])
    await db_session.execute(update(Task).where(Task.id == task["task_id"]).values(
        lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
    await db_session.commit()
    assert await recover_expired_tasks(db_session) == 1
    recovered = await load_durable_task(db_session, task["task_id"], str(test_user.id))
    assert recovered["status"] == "interrupted"
    assert recovered["retryable"] is True
    await retry_durable_task(db_session, task["task_id"], str(test_user.id))
    second = await claim_durable_task(db_session, task["task_id"])
    assert first["attempt_id"] != second["attempt_id"]
    assert await update_durable_attempt(db_session, task["task_id"], attempt_id=first["attempt_id"],
                                       expected_revision=first["revision"], status="completed",
                                       result={"stale": True}) is None


@pytest.mark.asyncio
async def test_remote_lease_loss_never_becomes_blindly_retryable(db_session, test_user):
    task, _ = await submit_durable_task(db_session, actor_id=str(test_user.id), task_type="wq_brain_submit", params={})
    await claim_durable_task(db_session, task["task_id"], lease_seconds=-1)
    await recover_expired_tasks(db_session)
    recovered = await load_durable_task(db_session, task["task_id"], str(test_user.id))
    assert recovered["status"] == "remote_outcome_unknown"
    assert not recovered["retryable"]
    with pytest.raises(TaskConflictError, match="TASK_NOT_RETRYABLE"):
        await retry_durable_task(db_session, task["task_id"], str(test_user.id))


@pytest.mark.asyncio
async def test_removed_project_member_cannot_read_cached_task(db_session, test_user):
    project = await create_project(db_session, test_user.id, name="research")
    await db_session.commit()
    task, _ = await _submit(db_session, test_user, project_id=str(project.id))
    member = await db_session.get(ProjectMember, (project.id, test_user.id))
    await db_session.delete(member)
    await db_session.commit()
    assert task["task_id"] in tasks
    with pytest.raises(ProjectAccessError):
        await load_durable_task(db_session, task["task_id"], str(test_user.id))


@pytest.mark.asyncio
async def test_worker_cannot_publish_after_project_membership_removed(db_session, test_user):
    project = await create_project(db_session, test_user.id, name="research")
    await db_session.commit()
    task, _ = await _submit(db_session, test_user, project_id=str(project.id))
    claimed = await claim_durable_task(db_session, task["task_id"])
    member = await db_session.get(ProjectMember, (project.id, test_user.id))
    await db_session.delete(member)
    await db_session.commit()
    with pytest.raises(ProjectAccessError):
        await update_durable_attempt(db_session, task["task_id"], attempt_id=claimed["attempt_id"],
                                     expected_revision=claimed["revision"], status="completed", result={"ok": 1})


@pytest.mark.asyncio
async def test_real_persistence_refuses_cancelled_worker_result(db_session, test_user, engine, monkeypatch):
    from quantgpt import db
    from quantgpt.task_store import persist_task_to_db_async

    task, _ = await _submit(db_session, test_user)
    claimed = await claim_durable_task(db_session, task["task_id"])
    stale = dict(claimed)
    await cancel_durable_task(db_session, task["task_id"], str(test_user.id))
    stale.update(status="completed", result={"late_result": True})
    monkeypatch.setattr(db, "_get_session_factory", lambda: async_sessionmaker(engine, expire_on_commit=False))
    await persist_task_to_db_async(task["task_id"], str(test_user.id), stale, strict=True)
    saved = await load_durable_task(db_session, task["task_id"], str(test_user.id))
    assert saved["status"] == "cancelled"
    assert saved["result"] is None


@pytest.mark.asyncio
async def test_mcp_status_uses_persisted_frozen_identity_after_eviction(db_session, test_user, engine, monkeypatch):
    from quantgpt import db, mcp_task_helper

    task, _ = await _submit(db_session, test_user)
    tasks.clear()
    monkeypatch.setattr(db, "_get_session_factory", lambda: async_sessionmaker(engine, expire_on_commit=False))
    monkeypatch.setattr(mcp_task_helper, "resolve_mcp_actor", lambda: test_user.id)
    result = await mcp_task_helper.get_mcp_task_status_payload_async(task["task_id"], include_result=True)
    assert result["status"] == "queued"
    assert result["user_id"] == str(test_user.id)
    assert result["params"] == {"expression": "close"}


@pytest.mark.asyncio
async def test_initial_persistence_failure_never_acknowledges_submission(monkeypatch):
    from quantgpt import mcp_task_helper

    async def unavailable(*args, **kwargs):
        raise RuntimeError("database unavailable")

    tasks.clear()
    monkeypatch.setattr(mcp_task_helper, "persist_task_to_db_async", unavailable)
    with pytest.raises(RuntimeError, match="database unavailable"):
        await mcp_task_helper.start_mcp_task("score", "close", {})
    assert not tasks


@pytest.mark.asyncio
async def test_claim_payload_is_independent_of_cache_and_cancel(db_session, test_user):
    task, _ = await _submit(db_session, test_user)
    claimed = await claim_durable_task(db_session, task["task_id"])
    attempt = claimed["attempt_id"]
    tasks[task["task_id"]]["params"]["expression"] = "mutated cache"
    await cancel_durable_task(db_session, task["task_id"], str(test_user.id))
    assert claimed["attempt_id"] == attempt and claimed["status"] == "running"
    assert claimed["cancelled"] is False and claimed["params"] == {"expression": "close"}


@pytest.mark.asyncio
@pytest.mark.parametrize("late_outcome", ["success", "cancel_exception"])
async def test_late_research_worker_cannot_mutate_new_attempt_cache(
    db_session, test_user, engine, monkeypatch, late_outcome,
):
    from quantgpt import db
    from quantgpt.research.jobs import run_research_job
    from quantgpt.task_store import CancelledException

    project = await create_project(db_session, test_user.id, name="Worker fencing")
    task, _ = await submit_durable_task(db_session, actor_id=str(test_user.id), project_id=str(project.id),
        task_type="research_evaluation", params={"kind": "factor", "config": {"backend": "local"}})
    original = await claim_durable_task(db_session, task["task_id"])
    started, release = threading.Event(), threading.Event()
    monkeypatch.setattr(db, "_get_session_factory", lambda: async_sessionmaker(engine, expire_on_commit=False))

    async def delayed_execution(*args, **kwargs):
        started.set()
        await asyncio.to_thread(release.wait, 10)
        if late_outcome == "cancel_exception":
            raise CancelledException("old publication attempt was fenced")
        return {"old_worker_result": True}

    monkeypatch.setattr("quantgpt.research.jobs.execute_research_request", delayed_execution)
    old_worker = asyncio.create_task(asyncio.to_thread(run_research_job, original))
    try:
        assert await asyncio.to_thread(started.wait, 5)
        await db_session.execute(update(Task).where(Task.id == task["task_id"]).values(
            lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
        await db_session.commit()
        assert await recover_expired_tasks(db_session) == 1
        await retry_durable_task(db_session, task["task_id"], str(test_user.id))
        current = await claim_durable_task(db_session, task["task_id"])
        release.set()
        await old_worker
        assert current["attempt_id"] != original["attempt_id"]
        assert tasks[task["task_id"]]["attempt_id"] == current["attempt_id"]
        assert tasks[task["task_id"]]["status"] == "running"
        assert tasks[task["task_id"]]["result"] is None
        db_session.expire_all()
        persisted = await load_durable_task(db_session, task["task_id"], original["user_id"])
        assert persisted["status"] == "running" and persisted["attempt_id"] == current["attempt_id"]
    finally:
        release.set()
        await old_worker


@pytest.mark.asyncio
async def test_stale_attempt_cannot_publish_while_retry_is_queued(db_session, test_user, engine, monkeypatch):
    from quantgpt import db
    from quantgpt.task_store import persist_task_to_db_async

    task, _ = await _submit(db_session, test_user)
    old = await claim_durable_task(db_session, task["task_id"])
    await db_session.execute(update(Task).where(Task.id == task["task_id"]).values(
        lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
    await db_session.commit()
    await recover_expired_tasks(db_session)
    await retry_durable_task(db_session, task["task_id"], str(test_user.id))
    old.update(status="completed", result={"old": True})
    monkeypatch.setattr(db, "_get_session_factory", lambda: async_sessionmaker(engine, expire_on_commit=False))
    with pytest.raises(TaskConflictError, match="STALE_ATTEMPT"):
        await persist_task_to_db_async(task["task_id"], str(test_user.id), old, strict=True)
    persisted = await load_durable_task(db_session, task["task_id"], str(test_user.id))
    assert persisted["status"] == "queued" and persisted["result"] is None


def test_strategy_cancel_during_compute_blocks_report_and_success(monkeypatch):
    from quantgpt.routes import strategy

    tasks.clear()
    tasks["cancel-race"] = {"task_id": "cancel-race", "user_id": "actor", "status": "running", "cancelled": False}

    def compute(*args):
        transition_task("cancel-race", "cancelled")
        return {"strategy_score": 99}

    monkeypatch.setattr(strategy, "_execute_strategy_backtest", compute)
    monkeypatch.setattr(strategy, "_execute_strategy_report", lambda *args: pytest.fail("report must not run after cancel"))
    monkeypatch.setattr(strategy, "persist_task_to_db", lambda *args: None)
    strategy._run_strategy_backtest_task("cancel-race", {}, "actor")
    assert tasks["cancel-race"]["status"] == "cancelled"
    assert "result" not in tasks["cancel-race"]


@pytest.mark.asyncio
async def test_sse_emits_same_stage_progress_with_snapshot_cursor(monkeypatch):
    from quantgpt.routes.backtest_tasks import stream_task

    monkeypatch.setenv("AUTH_DISABLED", "true")
    tasks.clear()
    tasks["sse-progress"] = {"task_id": "sse-progress", "status": "running", "progress": 10, "revision": 1}
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": [(b"last-event-id", b"999")]})
    response = await stream_task("sse-progress", request)
    events = response.body_iterator
    first = await anext(events)
    transition_task("sse-progress", progress=20)
    second = await anext(events)
    await events.aclose()
    first_payload = json.loads(str(first).split("data: ", 1)[1])
    second_payload = json.loads(str(second).split("data: ", 1)[1])
    assert first_payload["snapshot"] is True
    assert first_payload["resumed_from"] == "999"
    assert second_payload["status"] == "running"
    assert second_payload["progress"] == 20
    assert second_payload["revision"] > first_payload["revision"]
