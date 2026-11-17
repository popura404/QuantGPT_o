"""Bounded MCP calls for QuantGPT clients and read-only recovery of lost receipts."""

from __future__ import annotations

import asyncio
import json
import math
import os
from datetime import timedelta
from typing import Any

from anyio import BrokenResourceError, ClosedResourceError, EndOfStream
from mcp import ClientSession
from mcp.shared.exceptions import McpError
from mcp.types import CONNECTION_CLOSED, CallToolResult

_SUBMISSION_LOOKUP_FIELDS = {
    "evaluate_factors": ("idempotency_key",),
    "run_research_strategy": ("idempotency_key",),
    "submit_research_direction": ("round_id", "direction_id", "idempotency_key"),
    "submit_round_strategy": ("round_id", "spec"),
    "finalize_research_round": ("round_id", "selection_run_id"),
}


def request_timeout_seconds(value: float | str | None = None) -> float:
    """Resolve the per-request deadline used by the server and bundled clients."""
    raw = os.environ.get("QUANTGPT_MCP_REQUEST_TIMEOUT", "180") if value is None else value
    try:
        seconds = float(raw)
        if math.isfinite(seconds) and timedelta(seconds=seconds) > timedelta(0):
            return seconds
    except (TypeError, ValueError, OverflowError):
        pass
    raise ValueError("MCP request timeout must be a finite positive duration in seconds")


class McpCallFailure(RuntimeError):
    def __init__(self, payload: dict[str, Any]):
        self.payload = payload
        super().__init__(payload.get("message") or payload.get("hint") or payload["error_code"])


def _decode_text(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value.strip()
    return value


def _response(tool_name: str, arguments: dict[str, Any], reply: CallToolResult) -> Any:
    structured = reply.structuredContent
    if structured is not None:
        # FastMCP wraps tools annotated -> str; QuantGPT uses those strings for JSON.
        if set(structured) == {"result"}:
            value = _decode_text(structured["result"])
        else:
            value = structured
    else:
        blocks = [
            _decode_text(block.text) if block.type == "text" else block.model_dump(mode="json")
            for block in reply.content if block.type != "text" or block.text.strip()
        ]
        value = blocks[0] if len(blocks) == 1 else blocks or None
    if isinstance(value, dict) and value.get("error_code"):
        raise McpCallFailure(value)
    if isinstance(value, dict) and value.get("error") and set(value) <= {"error", "hint", "ok"}:
        raise McpCallFailure({"error_code": "MCP_TOOL_ERROR", "tool": tool_name, "message": str(value["error"])})
    if reply.isError:
        raise McpCallFailure({"error_code": "MCP_TOOL_ERROR", "tool": tool_name, "message": str(value or tool_name)})
    if value is None or value == "":
        raise McpCallFailure({"error_code": "MCP_EMPTY_RESPONSE", "tool": tool_name})
    if tool_name == "create_research_round":
        if not isinstance(value, dict) or not (value.get("round_id") or value.get("status") == "insufficient_data"):
            raise McpCallFailure({"error_code": "MCP_INVALID_RECEIPT", "tool": tool_name})
    default_submit = tool_name in _SUBMISSION_LOOKUP_FIELDS or tool_name == "validate_research_evaluation"
    if arguments.get("submit_only", default_submit):
        if not isinstance(value, dict) or not (value.get("task_id") or
                (tool_name == "submit_research_direction" and value.get("status") == "known_candidates")):
            raise McpCallFailure({"error_code": "MCP_INVALID_RECEIPT", "tool": tool_name})
    if tool_name == "find_research_submission":
        if not isinstance(value, dict) or not isinstance(value.get("found"), bool):
            raise McpCallFailure({"error_code": "MCP_INVALID_RECEIPT", "tool": tool_name})
        if value["found"] and not (value.get("task_id") and value.get("status")):
            raise McpCallFailure({"error_code": "MCP_INVALID_RECEIPT", "tool": tool_name})
    if tool_name == "get_mcp_task_status":
        if not isinstance(value, dict) or not value.get("status"):
            raise McpCallFailure({"error_code": "MCP_INVALID_TASK_STATUS", "task_id": arguments.get("task_id")})
        if not value.get("task_id") or value["task_id"] != arguments.get("task_id"):
            raise McpCallFailure({"error_code": "MCP_INVALID_TASK_STATUS", "task_id": arguments.get("task_id")})
        if value["status"] == "completed" and arguments.get("include_result") and value.get("result") in (None, {}, [], ""):
            raise McpCallFailure({"error_code": "MCP_MISSING_TASK_RESULT", "task_id": arguments.get("task_id"),
                                  "status": "completed", "next_action": "Inspect the recorded task and missing evidence."})
    return value


async def _call_once(client: ClientSession, tool_name: str, arguments: dict[str, Any], timeout: float) -> Any:
    try:
        # The SDK may make an additional tools/list call to validate an output
        # schema. Bound the whole invocation, including that extra round trip.
        async with asyncio.timeout(timeout):
            reply = await client.call_tool(tool_name, arguments)
    except McpError as exc:
        if exc.error.code == 408:
            raise McpCallFailure({"error_code": "MCP_REQUEST_TIMEOUT", "tool": tool_name, "timeout_seconds": timeout}) from exc
        if exc.error.code == CONNECTION_CLOSED:
            raise McpCallFailure({"error_code": "MCP_CONNECTION_LOST", "tool": tool_name}) from exc
        raise McpCallFailure({"error_code": "MCP_RPC_ERROR", "tool": tool_name, "message": str(exc)}) from exc
    except TimeoutError as exc:
        raise McpCallFailure({"error_code": "MCP_REQUEST_TIMEOUT", "tool": tool_name, "timeout_seconds": timeout}) from exc
    except (BrokenResourceError, ClosedResourceError, EndOfStream, OSError) as exc:
        raise McpCallFailure({"error_code": "MCP_CONNECTION_LOST", "tool": tool_name}) from exc
    except RuntimeError as exc:
        # The SDK rejects replies missing the tool's structured output before it
        # delivers their content (including empty replies) to the caller.
        raise McpCallFailure({"error_code": "MCP_INVALID_RESPONSE", "tool": tool_name}) from exc
    return _response(tool_name, arguments, reply)


def _recovery_query(tool_name: str, args: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    if args.get("task_id") and tool_name != "get_mcp_task_status":
        return "get_mcp_task_status", {"task_id": args["task_id"], "include_result": True}
    if not args.get("project_id"):
        return None
    project = {"project_id": args["project_id"]}
    if tool_name == "create_research_round":
        return "get_research_round", project
    fields = _SUBMISSION_LOOKUP_FIELDS.get(tool_name)
    if fields and args.get("submit_only", True) and all(args.get(key) for key in fields):
        return "find_research_submission", {**project, "submission_tool": tool_name,
                                            **{key: args[key] for key in fields}}
    if tool_name == "submit_research_direction" and args.get("round_id"):
        return "get_research_round", {**project, "round_id": args["round_id"]}
    return None


async def call_mcp_tool(
    client: ClientSession, tool_name: str, arguments: dict[str, Any], *, timeout_seconds: float | None = None,
) -> Any:
    """Call once. On a lost receipt, make at most one bounded read-only lookup."""
    timeout = request_timeout_seconds(timeout_seconds)
    try:
        return await _call_once(client, tool_name, arguments, timeout)
    except McpCallFailure as exc:
        if exc.payload["error_code"] not in {"MCP_REQUEST_TIMEOUT", "MCP_EMPTY_RESPONSE", "MCP_CONNECTION_LOST",
                                           "MCP_INVALID_RESPONSE", "MCP_INVALID_RECEIPT"}:
            raise
        exc.payload.update(outcome="unknown", next_action="Check the original task or request before any resubmission.")
        query = _recovery_query(tool_name, arguments)
        if query:
            read_tool, read_arguments = query
            exc.payload["reconciliation_tool"] = read_tool
            try:
                exc.payload["reconciliation"] = await _call_once(client, read_tool, read_arguments, timeout)
            except McpCallFailure as lookup_error:
                exc.payload["reconciliation"] = {"error_code": lookup_error.payload["error_code"]}
            except Exception as lookup_error:
                # Preserve the original submission outcome if the SDK rejects the lookup reply.
                exc.payload["reconciliation"] = {"error_code": "MCP_LOOKUP_FAILED", "message": str(lookup_error)}
        raise
