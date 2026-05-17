"""Tests for factor values MCP tool."""

import json

import pytest

from quantgpt import mcp_server


@pytest.mark.asyncio
async def test_mcp_compute_factor_values_returns_json(monkeypatch):
    def fake_payload(expression, universe="csi500", start_date="", end_date=""):
        return {
            "expression": expression,
            "universe": universe,
            "start_date": start_date,
            "end_date": end_date,
            "trading_days": 1,
            "data": [{"date": "2024-01-02", "values": {"A": 1.0}, "count": 1}],
        }

    monkeypatch.setattr(mcp_server, "_compute_factor_values_payload", fake_payload)

    result = json.loads(await mcp_server.compute_factor_values(
        "close",
        universe="small_scale",
        start_date="2024-01-02",
        end_date="2024-01-02",
    ))

    assert result["expression"] == "close"
    assert result["data"][0]["values"] == {"A": 1.0}


@pytest.mark.asyncio
async def test_mcp_compute_factor_values_returns_error(monkeypatch):
    def fake_payload(expression, universe="csi500", start_date="", end_date=""):
        raise ValueError("Invalid expression: bad")

    monkeypatch.setattr(mcp_server, "_compute_factor_values_payload", fake_payload)

    result = json.loads(await mcp_server.compute_factor_values("bad("))

    assert result == {"error": "Invalid expression: bad"}
