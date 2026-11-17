"""Recovery of the acceptance client's MCP requests using simulated replies only."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp.types import CallToolResult, TextContent

from quantgpt.mcp_client import McpCallFailure
from scripts import verify_multisecurity_round as verify


def reply(payload):
    return CallToolResult(content=[TextContent(type="text", text=json.dumps(payload))])


@pytest.mark.asyncio
@pytest.mark.parametrize("lost_receipt", [TimeoutError(), CallToolResult(content=[])])
@pytest.mark.parametrize("found", [True, False])
async def test_lost_submission_checks_exact_original_before_continuing(lost_receipt, found):
    receipt = {"found": True, "task_id": "original", "status": "running"} if found else {"found": False}
    client = SimpleNamespace(call_tool=AsyncMock(side_effect=[lost_receipt, reply(receipt)]))
    args = {"project_id": "p", "round_id": "r", "direction_id": "d", "direction_key": "private",
            "idempotency_key": "batch", "definitions": [{}]}
    if found:
        assert (await verify.call(client, "submit_research_direction", **args))["task_id"] == "original"
    else:
        with pytest.raises(McpCallFailure) as failure:
            await verify.call(client, "submit_research_direction", **args)
        assert failure.value.payload["outcome"] == "unknown"
    assert [call.args for call in client.call_tool.await_args_list] == [
        ("submit_research_direction", args),
        ("find_research_submission", {"project_id": "p", "round_id": "r", "direction_id": "d",
                                      "idempotency_key": "batch", "submission_tool": "submit_research_direction"}),
    ]


@pytest.mark.asyncio
async def test_round_creation_recovers_credentials_after_read_only_lookup():
    args = {"project_id": "p", "coordinator_key": "chosen-key", "directions": [{"name": "d"}]}
    created = {"round_id": "r", "directions": [{"direction_key": "recovered"}]}
    client = SimpleNamespace(call_tool=AsyncMock(side_effect=[TimeoutError(), reply({"round_id": "r"}), reply(created)]))
    assert await verify.call(client, "create_research_round", **args) == created
    assert [call.args for call in client.call_tool.await_args_list] == [
        ("create_research_round", args), ("get_research_round", {"project_id": "p"}), ("create_research_round", args)
    ]


@pytest.mark.asyncio
async def test_unresponsive_status_query_releases_the_agent_and_keeps_task_id(monkeypatch):
    async def stalled(*_):
        await asyncio.Event().wait()

    monkeypatch.setenv("QUANTGPT_MCP_REQUEST_TIMEOUT", "0.02")
    client = SimpleNamespace(call_tool=AsyncMock(side_effect=stalled))
    async with asyncio.timeout(3):
        with pytest.raises(McpCallFailure) as failure:
            await verify.wait_result(client, {"task_id": "original", "status": "running"})
    assert failure.value.payload["error_code"] == "MCP_REQUEST_TIMEOUT"
    assert "original" in " ".join(failure.value.__notes__)
    client.call_tool.assert_awaited_once_with("get_mcp_task_status", {"task_id": "original", "include_result": True})


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [{}, [], None])
async def test_completed_task_requires_evidence(result):
    task = {"task_id": "original", "status": "completed", "result": result}
    client = SimpleNamespace(call_tool=AsyncMock(return_value=reply(task)))
    with pytest.raises(RuntimeError, match="MCP_MISSING_TASK_RESULT|completed without"):
        await verify.wait_result(client, {"task_id": "original", "status": "running"})
    client.call_tool.assert_awaited_once_with("get_mcp_task_status", {"task_id": "original", "include_result": True})


@pytest.fixture
def virtual_clock(monkeypatch):
    now = 0

    async def sleep(seconds):
        nonlocal now
        now += seconds

    monkeypatch.setattr(verify, "time", SimpleNamespace(monotonic=lambda: now))
    monkeypatch.setattr(verify, "asyncio", SimpleNamespace(sleep=sleep))
    return lambda: now


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["queued", "running"])
async def test_heartbeat_only_stops_waiting_for_the_original_task(status, virtual_clock):
    def status_reply(*_):
        heartbeat = virtual_clock()
        return reply({"task_id": "original", "status": status, "revision": heartbeat, "updated_at": heartbeat,
                      "lease_expires_at": heartbeat + 60})

    client = SimpleNamespace(call_tool=AsyncMock(side_effect=status_reply))
    with pytest.raises(RuntimeError, match=f"Task original: no progress for 2s in {status}"):
        await verify.wait_result(client, {"task_id": "original", "status": status}, stall_timeout_seconds=2)
    assert virtual_clock() == 2
    assert all(call.args[0] == "get_mcp_task_status" for call in client.call_tool.await_args_list)


@pytest.mark.asyncio
async def test_real_progress_extends_wait_budget(virtual_clock):
    def status_reply(*_):
        now = virtual_clock()
        if now >= 3:
            return reply({"task_id": "original", "status": "completed", "result": {"validation": "ok"}})
        return reply({"task_id": "original", "status": "running", "progress_current": int(now)})

    client = SimpleNamespace(call_tool=AsyncMock(side_effect=status_reply))
    assert await verify.wait_result(client, {"task_id": "original", "status": "queued"},
                                    stall_timeout_seconds=2) == {"validation": "ok"}


@pytest.mark.parametrize("budget", ["0", "-1", "infinity"])
def test_cli_rejects_unbounded_stall_budget(budget):
    with pytest.raises(SystemExit) as err:
        verify.parse_args(["--demo", "--work-dir", "unused", "--stall-timeout-seconds", budget])
    assert err.value.code == 2
