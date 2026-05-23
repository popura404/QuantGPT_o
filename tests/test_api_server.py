"""API server middleware regressions."""

import pytest
from starlette.requests import Request

pytestmark = pytest.mark.asyncio


async def test_http_mcp_requires_configured_token(client, monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.delenv("QUANTGPT_MCP_HTTP_TOKEN", raising=False)

    resp = await client.get("/mcp")

    assert resp.status_code == 503
    assert "QUANTGPT_MCP_HTTP_TOKEN" in resp.json()["detail"]


async def test_http_mcp_rejects_invalid_token(client, monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setenv("QUANTGPT_MCP_HTTP_TOKEN", "expected-test-token")

    resp = await client.get("/mcp", headers={"Authorization": "Bearer wrong-token"})

    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid HTTP MCP token"


async def test_rejects_untrusted_host_before_routing(client):
    resp = await client.get("/api/v1/health", headers={"host": "evil.example"})

    assert resp.status_code == 400


async def test_mcp_path_detection_uses_scope_path_not_reconstructed_url():
    from quantgpt.api_server import _is_http_mcp_path, _request_scope_path

    scope = {
        "type": "http",
        "method": "GET",
        "scheme": "http",
        "path": "/mcp",
        "root_path": "",
        "query_string": b"",
        "headers": [(b"host", b"test/api")],
    }
    request = Request(scope)

    assert _request_scope_path(request) == "/mcp"
    assert _is_http_mcp_path(_request_scope_path(request)) is True
