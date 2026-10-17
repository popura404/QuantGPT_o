"""Legacy workbenches use the durable outbox and reject obsolete workers."""

import asyncio
import threading
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from quantgpt.models import Task
from quantgpt.routes import composite, iteration_routes
from quantgpt.task_executor import _dispatch_handler
from quantgpt.task_store import tasks, transition_task
from tests.test_iteration_routes import _ready_provenance


@pytest.fixture(autouse=True)
def clear_tasks():
    tasks.clear()
    yield
    tasks.clear()


@pytest.mark.asyncio
async def test_composite_acceptance_is_durable_and_idempotent(client, test_user, auth_headers, db_session, monkeypatch):
    dispatch = AsyncMock(return_value=False)
    monkeypatch.setattr(composite, "dispatch_durable_task", dispatch)
    body = {"factors": [{"expression": "close"}, {"expression": "volume"}], "universe": "small_scale"}
    headers = {**auth_headers, "Idempotency-Key": "composite-retry"}
    response = await client.post("/api/v1/composite-backtest", json=body, headers=headers)
    assert response.status_code == 202
    task_id = response.json()["task_id"]
    tasks.clear()  # Simulate loss of every process-local cache.
    record = await db_session.get(Task, task_id)
    assert record.status == "queued"
    assert record.dispatch_pending
    assert record.params["factors"][1]["expression"] == "volume"
    retry = await client.post("/api/v1/composite-backtest", json=body, headers=headers)
    assert retry.json()["task_id"] == task_id
    assert retry.json()["reused"]
    dispatch.assert_awaited_once()


@pytest.mark.asyncio
async def test_iteration_freezes_database_parent_for_restart(client, test_user, auth_headers, db_session, monkeypatch):
    monkeypatch.setattr(iteration_routes, "dispatch_durable_task", AsyncMock(return_value=False))
    parent = Task(id="parent-restart", user_id=test_user.id, status="completed", expression="rank(close)",
                  result={"params": {"universe": "small_scale"}, "metrics": {"sharpe": 1}})
    db_session.add(parent)
    await db_session.commit()
    response = await client.post("/api/v1/tasks/parent-restart/iterate", json={"n_candidates": 2, "direction": "volume"},
                                 headers=auth_headers)
    assert response.status_code == 202
    tasks.clear()
    record = await db_session.get(Task, response.json()["task_id"])
    assert record.status == "queued" and record.dispatch_pending
    assert record.params["direction"] == "volume"
    assert record.params["parent_snapshot"]["expression"] == "rank(close)"
    assert record.params["parent_snapshot"]["result"] == parent.result
    assert _dispatch_handler({"task_id": record.id, "task_type": "iteration", "params": record.params,
                              "user_id": str(test_user.id), "attempt_id": "claimed"}) is not None


@pytest.mark.asyncio
async def test_persisted_candidate_can_be_selected_after_restart(client, test_user, auth_headers, db_session):
    record = Task(id="iteration-restart", user_id=test_user.id, status="iteration_completed", task_type="iteration",
                  result={"candidates": [{"status": "success", "expression": "rank(close)",
                                           "validation_provenance": _ready_provenance()}]})
    db_session.add(record)
    await db_session.commit()
    response = await client.post("/api/v1/tasks/iteration-restart/select_candidate", json={"candidate_index": 0},
                                 headers=auth_headers)
    assert response.status_code == 200
    await db_session.refresh(record)
    assert record.result["selected_candidate_index"] == 0
    tasks.clear()
    reloaded = await client.get("/api/v1/tasks/iteration-restart", headers=auth_headers)
    assert reloaded.status_code == 200 and reloaded.json()["selected_candidate_index"] == 0


@pytest.mark.asyncio
async def test_cancel_before_iteration_report_commit_prevents_publication(engine, db_session, test_user, monkeypatch):
    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from quantgpt import task_store
    from quantgpt.models import Report

    factory = async_sessionmaker(engine, expire_on_commit=False)
    loop = asyncio.get_running_loop()
    monkeypatch.setattr("quantgpt.db._get_session_factory", lambda: factory)
    monkeypatch.setattr(task_store, "main_loop", loop)
    submitted, _ = await task_store.submit_durable_task(db_session, actor_id=str(test_user.id), task_type="iteration",
        params={"parent_snapshot": {"status": "completed", "expression": "rank(close)",
                                    "result": {"params": {"universe": "small_scale"}}}})
    claimed = await task_store.claim_durable_task(db_session, submitted["task_id"])
    monkeypatch.setattr(iteration_routes, "get_universe", lambda *args, **kwargs: ["A"])
    monkeypatch.setattr(iteration_routes.MarketDataFetcher, "fetch_stocks",
                        lambda *args, **kwargs: pd.DataFrame({"close": [10.0]}))
    candidate = {"status": "success", "expression": "rank(close)", "report_filename": "backtest_report_race.html"}

    def generate(**kwargs):
        kwargs["on_progress"](1, candidate)
        return [candidate]

    async def cancel():
        async with factory() as session:
            await task_store.cancel_durable_task(session, submitted["task_id"], str(test_user.id))

    def publish_after_cancel(task_id, user_id, payload, report_filename=None):
        if report_filename:
            asyncio.run_coroutine_threadsafe(cancel(), loop).result(timeout=5)
        task_store.persist_task_to_db(task_id, user_id, payload, report_filename)

    monkeypatch.setattr(iteration_routes, "generate_iteration_candidates", generate)
    monkeypatch.setattr(iteration_routes, "persist_task_to_db", publish_after_cancel)
    await asyncio.to_thread(iteration_routes._run_iteration_task, submitted["task_id"], "parent", str(test_user.id), 2,
                            attempt_id=claimed["attempt_id"])
    assert (await task_store.load_durable_task(db_session, submitted["task_id"], str(test_user.id)))["status"] == "cancelled"
    assert await db_session.scalar(select(func.count()).select_from(Report)) == 0


def test_cancelled_composite_cannot_publish_or_generate_report(monkeypatch):
    tasks["composite-cancel"] = {"task_id": "composite-cancel", "user_id": "user", "status": "running",
                                  "attempt_id": "first"}
    monkeypatch.setattr(composite, "fetch_market_data", lambda *args: (pd.DataFrame(), []))
    monkeypatch.setattr("quantgpt.task_store.persist_task_to_db", lambda *args: None)

    def cancel_during_compute(**kwargs):
        transition_task("composite-cancel", "cancelled")
        return {}

    monkeypatch.setattr("quantgpt.composite.run_composite_backtest", cancel_during_compute)
    monkeypatch.setattr("quantgpt.report.generate_report", lambda *args, **kwargs: pytest.fail("cancelled report published"))
    request = composite.CompositeBacktestRequest(factors=[{"expression": "close"}, {"expression": "volume"}])
    composite._run_composite_task("composite-cancel", request, "user", attempt_id="first")
    assert tasks["composite-cancel"]["status"] == "cancelled"
    assert "result" not in tasks["composite-cancel"]


@pytest.mark.parametrize("kind", ["composite", "iteration"])
def test_obsolete_worker_does_not_mutate_new_attempt(kind):
    tasks["retry"] = {"task_id": "retry", "status": "running", "attempt_id": "new"}
    if kind == "composite":
        request = composite.CompositeBacktestRequest(factors=[{"expression": "close"}, {"expression": "volume"}])
        composite._run_composite_task("retry", request, "user", attempt_id="old")
    else:
        iteration_routes._run_iteration_task("retry", "parent", "user", 2, attempt_id="old")
    assert tasks["retry"] == {"task_id": "retry", "status": "running", "attempt_id": "new"}


@pytest.mark.asyncio
async def test_comparison_data_wait_does_not_block_health(client, auth_headers, monkeypatch):
    from quantgpt.routes import comparison

    entered, release = threading.Event(), threading.Event()

    def blocked_fetch(*args):
        entered.set()
        if not release.wait(5):
            raise TimeoutError("test failed to release fetch")
        raise ValueError("offline test has no data")

    monkeypatch.setattr(comparison, "fetch_market_data", blocked_fetch)
    request = asyncio.create_task(client.post("/api/v1/compare-factors", headers=auth_headers,
        json={"factors": [{"expression": "close"}, {"expression": "volume"}]}))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        response = await asyncio.wait_for(client.get("/api/v1/health"), 1)
        assert response.status_code == 200
    finally:
        release.set()
        response = await request
    assert response.status_code == 400
