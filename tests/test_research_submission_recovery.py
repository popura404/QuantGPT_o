"""Timeout and receipt recovery with a memory MCP transport and isolated SQLite records."""

import asyncio
import hashlib
import json
import threading
import uuid
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from sqlalchemy import func, select

from quantgpt import mcp_task_helper
from quantgpt.mcp_client import McpCallFailure, call_mcp_tool
from quantgpt.mcp_server import QuantGPTMCP
from quantgpt.models import ResearchRound, Task
from quantgpt.research import rounds
from quantgpt.research.projects import ProjectAccessError, create_project
from quantgpt.research.rounds import describe_round, find_submission
from quantgpt.task_store import durable_idempotency_key
from scripts.verify_multisecurity_round import BASELINE, specification


@pytest.mark.asyncio
@pytest.mark.parametrize("sync_handler", [False, True])
async def test_native_tool_deadline_returns_error_and_next_request_works(monkeypatch, sync_handler):
    monkeypatch.setenv("QUANTGPT_MCP_REQUEST_TIMEOUT", "0.05")
    server = QuantGPTMCP("simulated")
    release = threading.Event()

    def slow_sync(label: str) -> str:
        release.wait(3)
        return label

    async def slow_async(label: str) -> str:
        await asyncio.Event().wait()
        return label

    server.add_tool(slow_sync if sync_handler else slow_async, name="silent")

    @server.tool()
    async def ping() -> str:
        return "alive"

    try:
        async with create_connected_server_and_client_session(server, timedelta(seconds=2)) as client:
            result = await client.call_tool("silent", {"label": "original"})
            assert result.isError
            error = json.loads(result.content[0].text)
            assert (error["error_code"], error["outcome"]) == ("MCP_REQUEST_TIMEOUT", "unknown")
            assert (await client.call_tool("ping", {})).content[0].text == "alive"
    finally:
        release.set()


@pytest.mark.asyncio
async def test_native_lost_receipt_reaches_the_same_task_via_one_read(monkeypatch):
    monkeypatch.setenv("QUANTGPT_MCP_REQUEST_TIMEOUT", "0.05")
    server = QuantGPTMCP("receipt-recovery")
    calls = []

    @server.tool()
    async def submit_research_direction(project_id: str, round_id: str, direction_id: str, idempotency_key: str) -> str:
        calls.append(("submit", project_id, round_id, direction_id, idempotency_key))
        await asyncio.Event().wait()
        return "unreachable"

    @server.tool()
    async def find_research_submission(project_id: str, round_id: str, direction_id: str, idempotency_key: str,
                                       submission_tool: str) -> str:
        assert submission_tool == "submit_research_direction"
        calls.append(("lookup", project_id, round_id, direction_id, idempotency_key))
        return json.dumps({"found": True, "task_id": "accepted", "status": "queued"})

    args = {"project_id": "p", "round_id": "r", "direction_id": "d", "idempotency_key": "one"}
    async with create_connected_server_and_client_session(server, timedelta(seconds=2)) as client:
        with pytest.raises(McpCallFailure) as failure:
            await call_mcp_tool(client, "submit_research_direction", args, timeout_seconds=1)
    assert failure.value.payload["error_code"] == "MCP_REQUEST_TIMEOUT"
    assert failure.value.payload["reconciliation"]["task_id"] == "accepted"
    assert calls == [("submit", "p", "r", "d", "one"), ("lookup", "p", "r", "d", "one")]


@pytest.mark.asyncio
@pytest.mark.parametrize(("kind", "status"), [("backtest", "interrupted"), ("wq_brain_submit", "remote_outcome_unknown")])
async def test_cancelled_rpc_cannot_publish_a_success_without_a_result(monkeypatch, kind, status):
    task_id = "fake"
    actor = str(uuid.uuid4())
    monkeypatch.setitem(mcp_task_helper.tasks, task_id, {"task_id": task_id, "user_id": actor,
                                                         "task_type": kind, "status": "running"})
    monkeypatch.setattr(mcp_task_helper, "resolve_mcp_actor", lambda: actor)
    save = AsyncMock()
    monkeypatch.setattr(mcp_task_helper, "persist_task_to_db_async", save)
    started = asyncio.Event()

    async def simulated_worker():
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            await mcp_task_helper.complete_mcp_task(task_id)

    worker = asyncio.create_task(simulated_worker())
    await started.wait()
    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await worker
    assert save.await_args.args[2]["status"] == status


@pytest.mark.asyncio
async def test_reconcile_only_the_original_request_and_never_requeue(db_session, test_user):
    actor = test_user.id
    project = await create_project(db_session, actor, name="synthetic receipts", market="us_stock")
    project_id = project.id
    assert (await describe_round(db_session, actor, project_id))["round_id"] is None
    round_id, direction, selection = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db_session.add(ResearchRound(id=round_id, project_id=project_id, coordinator_id=actor, coordinator_digest="mock",
                                 selection_config={}, holdout={}, frozen={"selection_run_id": str(selection)}))
    requests = [
        ("direction", f"direction:{direction}:original", "factor", {"round_id": str(round_id), "direction_id": str(direction)}),
        ("prior", f"direction:{direction}:previous", "factor", {"round_id": str(round_id), "direction_id": str(direction)}),
        ("baseline", "original", "strategy", {}),
        ("final", f"final:{round_id}", "round_final", {"round_id": str(round_id)}),
    ]
    for task_id, key, kind, params in requests:
        db_session.add(Task(id=task_id, user_id=actor, project_id=project_id, task_type="research_evaluation",
                            params={**params, "kind": kind}, status="interrupted", retryable=True,
                            idempotency_key=durable_idempotency_key(str(project_id), "research_evaluation", key),
                            dispatch_pending=False))
    await db_session.commit()
    assert await describe_round(db_session, actor, project_id) == {"round_id": str(round_id), "project_id": str(project_id)}

    async def lookup(tool, **identity):
        return await find_submission(db_session, actor, project_id, submission_tool=tool, **identity)

    args = {"round_id": round_id, "direction_id": direction, "idempotency_key": "original"}
    assert await lookup("submit_research_direction", **args) == {"found": True, "task_id": "direction", "status": "interrupted"}
    assert await lookup("submit_research_direction", **{**args, "round_id": uuid.uuid4()}) == {"found": False}
    assert await lookup("submit_research_direction", **{**args, "direction_id": uuid.uuid4()}) == {"found": False}
    assert await lookup("submit_research_direction", **{**args, "idempotency_key": "missing"}) == {"found": False}
    assert (await lookup("run_research_strategy", idempotency_key="original"))["task_id"] == "baseline"
    assert await lookup("evaluate_factors", idempotency_key="original") == {"found": False}
    assert (await lookup("finalize_research_round", round_id=round_id, selection_run_id=selection))["task_id"] == "final"
    assert await lookup("finalize_research_round", round_id=round_id, selection_run_id=uuid.uuid4()) == {"found": False}
    with pytest.raises(ProjectAccessError):
        await find_submission(db_session, uuid.uuid4(), project_id, submission_tool="submit_research_direction", **args)
    with pytest.raises(ProjectAccessError):
        await describe_round(db_session, uuid.uuid4(), project_id)
    assert await db_session.scalar(select(func.count()).select_from(Task)) == len(requests)
    assert await db_session.scalar(select(func.count()).select_from(Task).where(Task.dispatch_pending.is_(True))) == 0


@pytest.mark.asyncio
async def test_strategy_lookup_matches_only_the_submitted_spec(db_session, test_user, monkeypatch):
    actor = test_user.id
    project = await create_project(db_session, actor, name="strategy receipts", market="us_stock")
    config = {"scope": {"market": "us_stock", "universe_id": "mock"}, "direction": "higher_is_better",
              "simulation_config": {"rebalance_anchor_session": "2020-01-02", "rebalance_every_sessions": 5, "fees_bps": 10}}
    row = ResearchRound(project_id=project.id, coordinator_id=actor, coordinator_digest=hashlib.sha256(b"mock").hexdigest(),
                        selection_config=config, holdout={})
    db_session.add(row)
    await db_session.commit()
    reference = {"project_id": str(project.id), "evaluation_id": "mock", "backend": "local",
                 "evaluation_hash": "a" * 64, "definition_hash": "b" * 64}
    spec = specification(config, [(BASELINE, reference)], 2)
    # Exercise the actual enqueue identity with a synthetic component and no dispatcher.
    monkeypatch.setattr(rounds, "check_components", AsyncMock())
    monkeypatch.setattr("quantgpt.task_store.tasks", {})
    accepted = await rounds.submit_strategy(db_session, actor, project.id, round_id=row.id, coordinator_key="mock", spec=spec)
    args = {"round_id": row.id, "submission_tool": "submit_round_strategy"}
    receipt = await find_submission(db_session, actor, project.id, spec=spec, **args)
    assert receipt == {"found": True, "task_id": accepted["task_id"], "status": "queued"}
    assert await find_submission(db_session, actor, project.id, spec={**spec, "name": "different"}, **args) == {"found": False}
    assert await db_session.scalar(select(func.count()).select_from(Task)) == 1
