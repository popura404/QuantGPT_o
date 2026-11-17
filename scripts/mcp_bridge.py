"""Call QuantGPT over stdio or an existing Streamable HTTP MCP endpoint.

    python scripts/mcp_bridge.py --timeout-seconds 15 call get_research_round @args.json

Exit codes: 0 success, 1 tool or response error, 2 transport or argument error.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from contextlib import AsyncExitStack
from datetime import timedelta
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import McpError
from mcp.types import METHOD_NOT_FOUND

from quantgpt.mcp_client import McpCallFailure, call_mcp_tool, request_timeout_seconds


def _parse_args_payload(raw: str | None) -> dict[str, Any]:
    if raw is None or raw == "":
        return {}
    if raw.startswith("@"):
        payload = json.loads(Path(raw[1:]).read_text(encoding="utf-8"))
    else:
        payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("tool arguments must be a JSON object")
    return payload


async def _command(session: ClientSession, args: argparse.Namespace, timeout: float) -> Any:
    if args.command == "list":
        listing = await session.list_tools()
        rows = [
            {
                "name": tool.name,
                "description": (tool.description or "").strip().partition("\n")[0][:120],
            }
            for tool in listing.tools
        ]
        return {"count": len(rows), "tools": rows}
    if args.command == "schema":
        listing = await session.list_tools()
        for tool in listing.tools:
            if tool.name == args.tool:
                return tool.model_dump(mode="json")
        raise McpCallFailure({"error_code": "MCP_UNKNOWN_TOOL", "tool": args.tool})
    if args.command == "resources":
        try:
            listing = await session.list_resources()
        except McpError as exc:
            if exc.error.code != METHOD_NOT_FOUND:
                raise
            return {"resources": [], "note": "resources unsupported"}
        return {"count": len(listing.resources), "resources": [item.model_dump(mode="json") for item in listing.resources]}
    arguments = _parse_args_payload(args.args)
    if args.command == "prompt":
        return (await session.get_prompt(args.prompt, arguments)).model_dump(mode="json")
    return await call_mcp_tool(session, args.tool, arguments, timeout_seconds=timeout)


async def _run(args: argparse.Namespace, timeout: float) -> int:
    async with AsyncExitStack() as stack:
        if args.url:
            headers = {}
            if args.bearer_token_env:
                token = os.environ.get(args.bearer_token_env)
                if not token:
                    raise ValueError(f"Missing bearer token in {args.bearer_token_env}")
                headers["Authorization"] = f"Bearer {token}"
            http = await stack.enter_async_context(httpx.AsyncClient(headers=headers, timeout=timeout))
            read, write, _ = await stack.enter_async_context(streamable_http_client(args.url, http_client=http))
        else:
            server = StdioServerParameters(command=sys.executable, args=["-m", "quantgpt"],
                                           cwd=str(_PROJECT_ROOT), env=os.environ.copy())
            read, write = await stack.enter_async_context(stdio_client(server))
        session = await stack.enter_async_context(ClientSession(read, write, read_timeout_seconds=timedelta(seconds=timeout)))
        try:
            async with asyncio.timeout(timeout):
                await session.initialize()
            # Tool calls time the write and its optional read-only recovery separately.
            async with asyncio.timeout(None if args.command == "call" else timeout):
                result = await _command(session, args, timeout)
        except McpCallFailure as exc:
            print(json.dumps(exc.payload, ensure_ascii=False))
            return 2 if exc.payload["error_code"] in {"MCP_REQUEST_TIMEOUT", "MCP_CONNECTION_LOST"} else 1
        except McpError as exc:
            code = "MCP_REQUEST_TIMEOUT" if exc.error.code == 408 else "MCP_RPC_ERROR"
            print(json.dumps({"error_code": code, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
            return 2
        except TimeoutError:
            print(json.dumps({"error_code": "MCP_REQUEST_TIMEOUT", "timeout_seconds": timeout}), file=sys.stderr)
            return 2
        print(result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=2))
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Call QuantGPT MCP tools with a per-request deadline.")
    parser.add_argument("--timeout-seconds", type=float,
                        help="request deadline (default: QUANTGPT_MCP_REQUEST_TIMEOUT or 180 seconds)")
    parser.add_argument("--url", help="connect to a running Streamable HTTP MCP endpoint")
    parser.add_argument("--bearer-token-env", metavar="NAME",
                        help="with --url, use this environment variable as the bearer token")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="list every registered MCP tool")
    schema_parser = sub.add_parser("schema", help="print one tool's JSON schema")
    schema_parser.add_argument("tool")
    call_parser = sub.add_parser("call", help="invoke one tool")
    call_parser.add_argument("tool")
    call_parser.add_argument("args", nargs="?", default="{}", help="JSON object, or @path/to/args.json")
    sub.add_parser("resources", help="list MCP resources when the server exposes any")
    prompt_parser = sub.add_parser("prompt", help="render one MCP prompt")
    prompt_parser.add_argument("prompt")
    prompt_parser.add_argument("args", nargs="?", default="{}")

    args = parser.parse_args()
    if args.bearer_token_env and not args.url:
        parser.error("--bearer-token-env requires --url")
    try:
        return asyncio.run(_run(args, request_timeout_seconds(args.timeout_seconds)))
    except Exception as exc:
        print(json.dumps({"bridge_error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
