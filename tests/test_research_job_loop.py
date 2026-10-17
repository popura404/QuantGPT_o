"""Dispatched research jobs share the API loop even when its DB pool is full."""

import asyncio
import uuid

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool

from quantgpt import db, task_executor, task_store
from quantgpt.models import Base, Task, User
from quantgpt.research import jobs
from quantgpt.research.projects import create_project


@pytest.mark.asyncio
async def test_research_dispatch_uses_owner_loop_with_contended_file_pool(tmp_path, monkeypatch):
    owner_loop = asyncio.get_running_loop()
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'worker.sqlite'}",
        poolclass=AsyncAdaptedQueuePool, pool_size=1, max_overflow=0, pool_timeout=5)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db, "_get_session_factory", lambda: factory)
    monkeypatch.setattr(task_store, "tasks", {})
    monitors = set()
    monkeypatch.setattr(task_executor, "_worker_monitors", monitors)
    sql_loops, execution_loops, worker_errors = [], [], []
    execution_started, worker_finished = asyncio.Event(), asyncio.Event()
    original_worker = jobs.run_research_job

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def record_sql_loop(*args):
        sql_loops.append(asyncio.get_running_loop())

    async def execute(session, actor_id, project_id, params, **kwargs):
        execution_loops.append(asyncio.get_running_loop())
        owner_loop.call_soon_threadsafe(execution_started.set)
        assert await session.scalar(select(User.id).where(User.id == actor_id)) == actor_id
        return {"evaluated": True}

    def run_worker(payload, *, event_loop=None):
        try:
            original_worker(payload, event_loop=event_loop)
        except BaseException as exc:
            worker_errors.append(exc)
        finally:
            owner_loop.call_soon_threadsafe(worker_finished.set)

    monkeypatch.setattr(jobs, "execute_research_request", execute)
    monkeypatch.setattr(jobs, "run_research_job", run_worker)

    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        actor_id = uuid.uuid4()
        async with factory() as session:
            session.add(User(id=actor_id, email="loop-owner@example.com", is_active=True))
            await session.commit()
            project = await create_project(session, actor_id, name="Loop ownership")
            accepted, _ = await task_store.submit_durable_task(session, actor_id=str(actor_id),
                project_id=str(project.id), task_type="research_evaluation",
                params={"kind": "factor", "config": {"backend": "local"}})

        contender_started = asyncio.Event()

        async def api_contender():
            contender_started.set()
            async with engine.connect() as connection:
                assert await connection.scalar(text("SELECT 1")) == 1

        # Bind the pool's wait queue to the API loop through real contention.
        async with engine.connect():
            contender = asyncio.create_task(api_contender())
            await contender_started.wait()
            assert not contender.done()
        await contender

        async with factory() as dispatch_session:
            assert await task_executor.dispatch_durable_task(dispatch_session, accepted["task_id"])
            # claim_durable_task's refresh holds the only connection. The job's
            # first DB request must wait on the same queue before this closes it.
            await asyncio.wait_for(execution_started.wait(), timeout=10)
        await asyncio.wait_for(worker_finished.wait(), timeout=10)
        assert not worker_errors
        assert execution_loops == [owner_loop]
        assert all(loop is owner_loop for loop in sql_loops)
        async with factory() as session:
            saved = await session.get(Task, accepted["task_id"])
            assert saved is not None
            assert saved.status == "completed", saved.error
            assert saved.result == {"evaluated": True}
    finally:
        for monitor in list(monitors):
            monitor.cancel()
        await asyncio.gather(*monitors, return_exceptions=True)
        await engine.dispose()
