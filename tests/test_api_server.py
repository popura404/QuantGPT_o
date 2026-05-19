"""API server middleware regressions."""

import pytest

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
