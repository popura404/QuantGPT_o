"""Two real stdio research servers share SQLite and survive the loss of one owner."""

import asyncio
import json
import shutil
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from quantgpt.models import Base, EvaluationExecutionLease, Experiment, ResearchAuditEvent, Task, User
from quantgpt.research.demo import prepare_offline_demo
from quantgpt.research.projects import create_project

# Only the first numerical call in this dedicated test subprocess stalls. The
# controller signals the process to exit while it still holds both leases.
STALLING_SERVER = """
import os
import time
from pathlib import Path
from quantgpt import backtest
from quantgpt.mcp_server import mcp
directory = Path(os.environ["QUANTGPT_TEST_FAULT_DIR"])
def stall(*args, **kwargs):
    (directory / "computing").touch()
    until = time.monotonic() + 90
    while time.monotonic() < until and not (directory / "exit-owner").is_file():
        time.sleep(.05)
    (directory / "owner-exiting").touch()
    os._exit(17)
backtest.run_factor_backtest = stall
mcp.run(transport="stdio")
"""


@asynccontextmanager
async def _client(environment, checkout, *, stall=False):
    args = ["-c", STALLING_SERVER] if stall else ["-m", "quantgpt", "--transport", "stdio"]
    params = StdioServerParameters(command=sys.executable, args=args, cwd=checkout, env=environment)
    async with stdio_client(params) as streams, ClientSession(*streams) as client:
        await client.initialize()
        yield client


async def _call(client, name, args):
    result = await client.call_tool(name, args)
    assert not result.isError, result
    return json.loads(result.content[0].text)


async def _result(client, task_id):
    async with asyncio.timeout(45):
        while True:
            payload = await _call(client, "get_mcp_task_status", {"task_id": task_id, "include_result": True})
            if payload.get("status") == "completed":
                return payload["result"]
            assert payload.get("status") in {"queued", "running"}, payload
            await asyncio.sleep(.1)


@pytest.mark.asyncio
async def test_separate_stdio_servers_recover_same_task_after_owner_exit(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'worker.sqlite'}"
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    actor = uuid.uuid4()
    root = tmp_path / "snapshots"
    demo = prepare_offline_demo(root)
    # Both deployments use one source snapshot. Editing the checkout while
    # clients run would correctly trigger CODE_VERSION_CHANGED on a replay.
    checkout = tmp_path / "deployment"
    repository = Path(__file__).resolve().parents[1]
    shutil.copytree(repository / "quantgpt", checkout / "quantgpt", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(repository / "requirements", checkout / "requirements")
    env = {"DATABASE_URL": url, "QUANTGPT_RESEARCH_SNAPSHOT_ROOT": str(root), "QUANTGPT_MCP_STDIO_USER_ID": str(actor),
           "QUANTGPT_TEST_FAULT_DIR": str(tmp_path), "QUANTGPT_CACHE_ONLY": "true", "QUANTGPT_TASK_BACKEND": "thread",
           "AUTH_DISABLED": "false", "JWT_SECRET_KEY": "test-secret-key-for-ci-only-do-not-use-in-production"}
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as session:
            session.add(User(id=actor, email="stdio-workers@example.invalid", is_active=True))
            await session.flush()
            project = await create_project(session, actor, name="stdio workers")
            project_id = str(project.id)
            session.add(ResearchAuditEvent(project_id=project.id, actor_id=actor, action="snapshot_registered",
                payload={"snapshot_id": demo["config"]["data_inputs"][0]["manifest_id"]}))
            await session.commit()

        async def submit(client, credential, expression, key):
            request = {**credential, "idempotency_key": key,
                       "definitions": [{**demo["definitions"][0], "expression": expression}]}
            return (await _call(client, "submit_research_direction", request))["task_id"]

        async with _client({**env, "QUANTGPT_MAX_ACTIVE_TASKS": "1"}, checkout, stall=True) as owner:
            config = demo["config"]
            config["window"]["end_session"] = "2024-05-01"
            created = await _call(owner, "create_research_round", {"project_id": project_id, "config": config,
                "holdout_start": "2024-06-03", "holdout_end": "2024-09-02",
                "directions": [{"name": name, "hypothesis": f"Predeclared {name}"} for name in
                               ("trend", "independent", "reversal", "average")]})
            scope = {"project_id": project_id, "round_id": created["round_id"]}
            credentials = {direction["name"]: {**scope, "direction_id": direction["direction_id"],
                "direction_key": direction["direction_key"]} for direction in created["directions"]}
            original = await submit(owner, credentials["trend"], "rank(close)", "original")
            async with asyncio.timeout(30):
                while not (tmp_path / "computing").is_file():
                    await asyncio.sleep(.05)
            async with _client(env, checkout) as survivor:
                duplicate = await submit(survivor, credentials["independent"], "rank(close)", "different-proposal")
                abandoned = await submit(survivor, credentials["independent"], "rank(close)", "cancel-only-this-task")
                generic = await _call(survivor, "cancel_mcp_task", {"task_id": original})
                assert generic["error_code"] == "ROUND_TASK_CREDENTIAL_REQUIRED"
                wrong_direction = await _call(survivor, "cancel_research_round_task",
                                              {**credentials["reversal"], "task_id": abandoned})
                assert wrong_direction["error_code"] == "ROUND_OR_CREDENTIAL_UNAVAILABLE"
                cancelled = await _call(survivor, "cancel_research_round_task",
                                        {**credentials["independent"], "task_id": abandoned})
                assert cancelled["status"] == "cancelled"
                reversal = await submit(survivor, credentials["reversal"], "-rank(close)", "reversal")
                assert (await _result(survivor, reversal))["evaluations"][0]["summary"]["performance_observed"]
                (tmp_path / "exit-owner").touch()
                async with asyncio.timeout(10):
                    while not (tmp_path / "owner-exiting").is_file():
                        await asyncio.sleep(.05)
                async with factory() as session:
                    past = datetime.now(timezone.utc) - timedelta(minutes=1)
                    # Move both expiry clocks forward without waiting a minute.
                    await session.execute(update(Task).where(Task.id == original).values(lease_expires_at=past))
                    await session.execute(update(EvaluationExecutionLease).where(
                        EvaluationExecutionLease.evaluation_id.in_(select(Experiment.experiment_id).where(
                            Experiment.expression == "rank(close)"))).values(expires_at=past))
                    await session.commit()
                average = await submit(survivor, credentials["average"], "rank(ts_mean(close, 5))", "average")
                recovered, joined, independent = await asyncio.gather(*(_result(survivor, tid) for tid in
                                                                         (original, duplicate, average)))
                assert independent["evaluations"][0]["summary"]["performance_observed"]
                assert recovered["evaluations"] == joined["evaluations"]
                assert recovered["batch_diagnostics"]["engine_calls"] + joined["batch_diagnostics"]["engine_calls"] == 1
                assert original == await submit(survivor, credentials["trend"], "rank(close)", "original")
                for credential in credentials.values():
                    await _call(survivor, "finish_research_direction", credential)
                summary = await _call(survivor, "get_research_round", scope)
                assert all(branch["finished"] for branch in summary["directions"])
                states = {task["task_id"]: task["status"] for branch in summary["directions"] for task in branch["tasks"]}
                assert states == {tid: "completed" for tid in (original, duplicate, reversal, average)} | {abandoned: "cancelled"}
        async with factory() as session:
            assert len((await session.scalars(select(Experiment))).all()) == 3
            assert not (await session.scalars(select(EvaluationExecutionLease))).all()
    finally:
        await engine.dispose()
