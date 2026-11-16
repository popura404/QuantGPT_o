"""Local factor branches on independent connections to the same project."""

import asyncio
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from quantgpt.models import Base, EvaluationExecutionLease, Experiment, ResearchAuditEvent, Task, User
from quantgpt.research.contracts import EvaluationConfigV1, FactorDefinitionV1
from quantgpt.research.demo import prepare_offline_demo
from quantgpt.research.evaluation_leases import ExecutionLeaseLost, evaluation_execution, fence_evaluation_execution
from quantgpt.research.evaluations import evaluate_factor_batch, register_evaluation
from quantgpt.research.hypotheses import register_hypothesis
from quantgpt.research.projects import create_project
from quantgpt.task_store import claim_durable_task, recover_expired_tasks, submit_durable_task


@pytest.mark.asyncio
async def test_parallel_branches_reuse_evaluation_without_repeating_numerical_work(tmp_path, monkeypatch):
    from quantgpt import backtest

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'branches.sqlite'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    root = tmp_path / "snapshots"
    demo = prepare_offline_demo(root)
    config = EvaluationConfigV1.model_validate(demo["config"])
    first = FactorDefinitionV1.model_validate(demo["definitions"][0])
    second = first.model_copy(update={"expression": "-rank(close)"})
    actor = uuid.uuid4()
    third = first.model_copy(update={"expression": "rank(ts_mean(close, 3))"})
    fourth = first.model_copy(update={"expression": "rank(ts_delta(close, 3))"})
    # Four distinct numerical evaluations must overlap even with a fifth
    # caller waiting on the first evaluation's identity.
    simultaneous = threading.Barrier(4, timeout=30)
    calls = []
    calculate = backtest.run_factor_backtest

    def observed(*args, **kwargs):
        calls.append(kwargs["expression"])
        simultaneous.wait()
        return calculate(*args, **kwargs)

    monkeypatch.setattr(backtest, "run_factor_backtest", observed)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as session:
            session.add(User(id=actor, email="branches@example.invalid", is_active=True))
            await session.flush()
            project = await create_project(session, actor, name="Branches")
            project_id = project.id
            session.add(ResearchAuditEvent(project_id=project_id, actor_id=actor, action="snapshot_registered",
                payload={"snapshot_id": config.data_inputs[0].manifest_id}))
            await session.commit()

        async def submit(definition):
            async with factory() as session:
                await register_hypothesis(session, actor, project_id, definition,
                                          economic_hypothesis=definition.expression)
                await session.commit()
                return await evaluate_factor_batch(session, actor, project_id, [definition], config, snapshot_root=root)

        inputs = [first, second, third, fourth, first]
        results = await asyncio.wait_for(asyncio.gather(*(submit(item) for item in inputs)), timeout=80)
        assert sorted(calls) == sorted(item.expression for item in inputs[:4])
        assert sum(item["batch_diagnostics"]["engine_calls"] for item in results) == 4
        assert results[0]["evaluations"] == results[4]["evaluations"]
        assert all(item["evaluations"][0]["summary"]["performance_observed"] for item in results)
        async with factory() as session:
            assert len((await session.scalars(select(Experiment))).all()) == 4
            assert not (await session.scalars(select(EvaluationExecutionLease))).all()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_expired_owner_and_disconnected_local_task_resume(db_session, test_user):
    from tests.test_research_hypotheses import config, definition

    actor = test_user.id
    project = await create_project(db_session, actor, name="Recovery")
    row, _ = await register_evaluation(db_session, actor, project.id, definition(), config())
    evaluation_id = str(row.experiment_id)
    db_session.add(EvaluationExecutionLease(evaluation_id=evaluation_id, owner_token="stale",
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
    await db_session.commit()
    params = {"kind": "factor", "config": {"backend": "local"}}
    payload, _ = await submit_durable_task(db_session, actor_id=str(actor), project_id=str(project.id),
        task_type="research_evaluation", params=params, idempotency_key="restart")
    task_id = payload["task_id"]
    previous = await claim_durable_task(db_session, task_id, lease_seconds=-1)
    assert previous
    assert await recover_expired_tasks(db_session) == 1
    resumed, created = await submit_durable_task(db_session, actor_id=str(actor), project_id=str(project.id),
        task_type="research_evaluation", params=params, idempotency_key="restart")
    assert not created and resumed["task_id"] == task_id and resumed["status"] == "queued"
    new = await claim_durable_task(db_session, task_id)
    assert new and new["attempt_id"] != previous["attempt_id"]
    async with evaluation_execution(db_session, evaluation_id) as token:
        assert token and token != "stale"
        with pytest.raises(ExecutionLeaseLost):
            await fence_evaluation_execution(db_session, evaluation_id, "stale")
        await fence_evaluation_execution(db_session, evaluation_id, token)
        row.result_summary = {"performance_observed": True}
        await db_session.commit()
    async with evaluation_execution(db_session, evaluation_id) as token:
        assert token is None

    # An interrupted task saved by an older version can be resumed with its
    # original idempotency key after upgrading.
    await db_session.execute(update(Task).where(Task.id == task_id).values(status="interrupted", retryable=True))
    await db_session.commit()
    resumed, created = await submit_durable_task(db_session, actor_id=str(actor), project_id=str(project.id),
        task_type="research_evaluation", params=params, idempotency_key="restart")
    assert not created and resumed["status"] == "queued"


@pytest.mark.asyncio
async def test_live_owner_renews_until_cancelled(tmp_path, monkeypatch):
    from quantgpt.research import evaluation_leases
    from tests.test_research_hypotheses import config, definition

    monkeypatch.setattr(evaluation_leases, "LEASE_SECONDS", 0.9)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'renew.sqlite'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ready = asyncio.Event()

    async def calculate():
        async with factory() as session, evaluation_execution(session, evaluation_id) as token:
            assert token
            ready.set()
            await asyncio.Event().wait()

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as db_session:
            actor = uuid.uuid4()
            db_session.add(User(id=actor, email="lease-owner@example.invalid", is_active=True))
            await db_session.flush()
            project = await create_project(db_session, actor, name="Renewal")
            row, _ = await register_evaluation(db_session, actor, project.id, definition(), config())
            evaluation_id = str(row.experiment_id)
            await db_session.commit()
        worker = asyncio.create_task(calculate())
        try:
            await asyncio.wait_for(ready.wait(), timeout=10)
            async with factory() as inspector:
                start = (await inspector.get(EvaluationExecutionLease, evaluation_id)).expires_at
            async with asyncio.timeout(5):
                while True:
                    async with factory() as inspector:
                        current = await inspector.get(EvaluationExecutionLease, evaluation_id)
                        assert current
                        if current.expires_at > start:
                            break
                    await asyncio.sleep(.05)
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        async with factory() as follower:
            assert await follower.get(EvaluationExecutionLease, evaluation_id) is None
            async with evaluation_execution(follower, evaluation_id) as token:
                assert token
                await fence_evaluation_execution(follower, evaluation_id, token)
                await follower.commit()
    finally:
        await engine.dispose()
