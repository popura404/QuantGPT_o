"""Exercise MCP deadlines and receipt handling without starting a QuantGPT worker."""

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp.shared.exceptions import McpError
from mcp.types import CallToolResult, ErrorData, TextContent

from quantgpt.mcp_client import McpCallFailure, call_mcp_tool, request_timeout_seconds
from scripts import mcp_bridge


def text_reply(value, *, is_error=False):
    return CallToolResult(content=[TextContent(type="text", text=json.dumps(value))], isError=is_error)


@pytest.mark.asyncio
async def test_unanswered_write_looks_up_original_without_resubmitting():
    calls = []

    async def handle(name, args):
        calls.append((name, args))
        if name == "submit_research_direction":
            await asyncio.Event().wait()
        return text_reply({"found": True, "task_id": "original", "status": "queued"})

    args = {"project_id": "project", "round_id": "round", "direction_id": "branch",
            "direction_key": "private", "idempotency_key": "batch"}
    with pytest.raises(McpCallFailure) as failure:
        await call_mcp_tool(SimpleNamespace(call_tool=handle), "submit_research_direction", args, timeout_seconds=.02)
    assert failure.value.payload["error_code"] == "MCP_REQUEST_TIMEOUT"
    assert failure.value.payload["reconciliation"] == {"found": True, "task_id": "original", "status": "queued"}
    assert calls == [
        ("submit_research_direction", args),
        ("find_research_submission", {"submission_tool": "submit_research_direction", "project_id": "project",
                                      "round_id": "round", "direction_id": "branch", "idempotency_key": "batch"}),
    ]
    assert "private" not in json.dumps(failure.value.payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("lost", [
    text_reply({"error_code": "MCP_REQUEST_TIMEOUT", "outcome": "unknown"}, is_error=True),
    McpError(ErrorData(code=408, message="request timed out")),
    CallToolResult(content=[]),
    CallToolResult(content=[], structuredContent={"result": None}),
    CallToolResult(content=[TextContent(type="text", text=" ")]),
    RuntimeError("Tool evaluate_factors has an output schema but did not return structured content"),
])
async def test_missing_receipt_and_unsuccessful_lookup_remain_unknown(lost):
    send = AsyncMock(side_effect=[lost, TimeoutError()])
    with pytest.raises(McpCallFailure) as failure:
        await call_mcp_tool(SimpleNamespace(call_tool=send), "evaluate_factors",
                            {"project_id": "p", "idempotency_key": "k"})
    assert failure.value.payload["outcome"] == "unknown"
    assert failure.value.payload["reconciliation"]["error_code"] == "MCP_REQUEST_TIMEOUT"
    assert [call.args[0] for call in send.await_args_list] == ["evaluate_factors", "find_research_submission"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("reply", "expected"), [
    (CallToolResult(content=[], structuredContent={}), {}),
    (CallToolResult(content=[], structuredContent={"result": "[]"}), []),
    (CallToolResult(content=[], structuredContent={"result": []}), []),
    (text_reply({"projects": []}), {"projects": []}),
])
async def test_explicit_empty_collection_is_a_valid_read(reply, expected):
    send = AsyncMock(return_value=reply)
    assert await call_mcp_tool(SimpleNamespace(call_tool=send), "list_research_projects", {}) == expected
    send.assert_awaited_once()


@pytest.mark.asyncio
async def test_business_error_and_valid_research_blockers_keep_their_meaning():
    send = AsyncMock(side_effect=[
        CallToolResult(content=[], structuredContent={"result": json.dumps({"error_code": "ROUND_CLOSED"})}),
        text_reply({"status": "known_candidates", "created": False}),
        text_reply({"status": "insufficient_data", "missing_sessions": 10}),
    ])
    client = SimpleNamespace(call_tool=send)
    with pytest.raises(McpCallFailure, match="ROUND_CLOSED"):
        await call_mcp_tool(client, "submit_research_direction", {})
    assert (await call_mcp_tool(client, "submit_research_direction", {}))["status"] == "known_candidates"
    assert (await call_mcp_tool(client, "create_research_round", {}))["status"] == "insufficient_data"
    assert send.await_count == 3


@pytest.mark.asyncio
async def test_error_only_reply_and_status_without_identity_fail_closed():
    client = SimpleNamespace(call_tool=AsyncMock(side_effect=[
        text_reply({"error": "data service unavailable"}), text_reply({"status": "running"}),
    ]))
    with pytest.raises(McpCallFailure, match="data service unavailable"):
        await call_mcp_tool(client, "get_stock_history", {})
    with pytest.raises(McpCallFailure, match="MCP_INVALID_TASK_STATUS"):
        await call_mcp_tool(client, "get_mcp_task_status", {"task_id": "original"})


@pytest.mark.parametrize("invalid", ["0", -1, "nan", "inf", "abc", "1e99"])
def test_request_deadline_requires_finite_positive_seconds(monkeypatch, invalid):
    monkeypatch.setenv("QUANTGPT_MCP_REQUEST_TIMEOUT", "12.5")
    assert request_timeout_seconds() == 12.5
    assert request_timeout_seconds(2) == 2
    with pytest.raises(ValueError, match="finite positive"):
        request_timeout_seconds(invalid)


@pytest.mark.asyncio
@pytest.mark.parametrize(("reply", "exit_code"), [
    (CallToolResult(content=[]), 1),
    (text_reply({"error_code": "MARKET_DATA_UNAVAILABLE"}), 1),
    (text_reply({"projects": []}), 0),
    (McpError(ErrorData(code=408, message="timeout")), 2),
])
async def test_shell_bridge_exit_status_and_connection_deadline(monkeypatch, capsys, reply, exit_code):
    @asynccontextmanager
    async def transport(_server):
        yield (None, None)

    @asynccontextmanager
    async def session(_read, _write, *, read_timeout_seconds):
        assert read_timeout_seconds == timedelta(seconds=1)
        call_tool = AsyncMock(side_effect=reply) if isinstance(reply, Exception) else AsyncMock(return_value=reply)
        yield SimpleNamespace(initialize=AsyncMock(), call_tool=call_tool)

    monkeypatch.setattr(mcp_bridge, "stdio_client", transport)
    monkeypatch.setattr(mcp_bridge, "ClientSession", session)
    options = SimpleNamespace(url=None, command="call", tool="list_research_projects", args="{}")
    assert await mcp_bridge._run(options, 1) == exit_code
    output = json.loads(capsys.readouterr().out)
    assert bool(output.get("error_code")) == (exit_code != 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("token_env", [None, "DUMMY_MCP_TOKEN"])
async def test_http_handshake_is_bounded_and_auth_is_opt_in(monkeypatch, capsys, token_env):
    monkeypatch.setenv("DUMMY_MCP_TOKEN", "fake-token")
    monkeypatch.setenv("QUANTGPT_MCP_HTTP_TOKEN", "unused-token")

    @asynccontextmanager
    async def transport(_url, *, http_client):
        expected = "Bearer fake-token" if token_env else None
        assert http_client.headers.get("Authorization") == expected
        yield (None, None, None)

    @asynccontextmanager
    async def session(*_args, **_kwargs):
        yield SimpleNamespace(initialize=asyncio.Event().wait)

    monkeypatch.setattr(mcp_bridge, "streamable_http_client", transport)
    monkeypatch.setattr(mcp_bridge, "ClientSession", session)
    options = SimpleNamespace(url="https://example.invalid/mcp", bearer_token_env=token_env, command="list")
    assert await mcp_bridge._run(options, .02) == 2
    output = capsys.readouterr().err
    assert json.loads(output)["error_code"] == "MCP_REQUEST_TIMEOUT"
    assert "fake-token" not in output and "unused-token" not in output
