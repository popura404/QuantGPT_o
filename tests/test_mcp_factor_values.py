"""Tests for factor values MCP tool."""

import json
from unittest.mock import Mock

import pytest

from quantgpt import mcp_server


@pytest.fixture(autouse=True)
def mcp_task_fakes(monkeypatch):
    async def fake_start(*args, **kwargs):
        return "task-factor-values"

    async def fake_complete(*args, **kwargs):
        return None

    async def fake_progress(*args, **kwargs):
        return None

    monkeypatch.setattr(mcp_server, "start_mcp_task", fake_start)
    monkeypatch.setattr(mcp_server, "complete_mcp_task", fake_complete)
    monkeypatch.setattr(mcp_server, "update_mcp_task_progress", fake_progress)


@pytest.mark.asyncio
async def test_mcp_compute_factor_values_returns_json(monkeypatch):
    calls = {}

    def fake_payload(
        expression,
        universe="csi500",
        start_date="",
        end_date="",
        allow_remote_fetch=False,
        universe_date=None,
        **kwargs,
    ):
        calls["allow_remote_fetch"] = allow_remote_fetch
        calls["universe_date"] = universe_date
        return {
            "expression": expression,
            "universe": universe,
            "start_date": start_date,
            "end_date": end_date,
            "universe_date": universe_date,
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
    assert result["universe_date"] == "2024-01-02"
    assert result["data"][0]["values"] == {"A": 1.0}
    assert calls["allow_remote_fetch"] is False
    assert calls["universe_date"] == "2024-01-02"


@pytest.mark.asyncio
async def test_mcp_compute_factor_values_returns_error(monkeypatch):
    def fake_payload(
        expression,
        universe="csi500",
        start_date="",
        end_date="",
        allow_remote_fetch=False,
        universe_date=None,
        **kwargs,
    ):
        raise ValueError("Invalid expression: bad")

    monkeypatch.setattr(mcp_server, "_compute_factor_values_payload", fake_payload)

    result = json.loads(await mcp_server.compute_factor_values("bad("))

    assert result == {"error": "Invalid expression: bad"}


@pytest.mark.asyncio
async def test_mcp_us_capability_blocker_retains_structured_fields(monkeypatch):
    from quantgpt.strategy.us_adapter import USResearchAdapter
    from quantgpt.us_data.contracts import DataCapabilityError

    adapter = Mock(spec=USResearchAdapter)
    adapter.capabilities.return_value.universes = ("test_cohort",)
    adapter.capabilities.return_value.data_fields = ()
    adapter.get_universe.return_value = ["test:IBM"]
    adapter.fetch_factor_market_data.side_effect = DataCapabilityError(
        "PRICE_COVERAGE_INCOMPLETE", "Requested history is incomplete", next_action="configure_licensed_history",
    )
    lookup = Mock(return_value=adapter)
    monkeypatch.setattr("quantgpt.factor_values.get_adapter", lookup)
    result = json.loads(await mcp_server.compute_factor_values("close", universe="test_cohort", market="us",
                                                              start_date="2024-01-02", end_date="2024-01-03",
                                                              allow_remote_fetch=True))
    lookup.assert_called_with("us")
    adapter.fetch_factor_market_data.assert_called_once()
    assert result["error_code"] == "PRICE_COVERAGE_INCOMPLETE"
    assert result["retryable"] is False
    assert result["next_action"] == "configure_licensed_history"


@pytest.mark.asyncio
async def test_mcp_compute_factor_values_explicit_universe_date_wins(monkeypatch):
    calls = {}

    def fake_payload(
        expression,
        universe="csi500",
        start_date="",
        end_date="",
        allow_remote_fetch=False,
        universe_date=None,
        **kwargs,
    ):
        calls["universe_date"] = universe_date
        calls["allow_remote_fetch"] = allow_remote_fetch
        return {"expression": expression, "universe_date": universe_date, "trading_days": 0, "data": []}

    monkeypatch.setattr(mcp_server, "_compute_factor_values_payload", fake_payload)
    monkeypatch.setattr(mcp_server, "_raise_if_remote_prefetch_required", lambda **_kwargs: None)
    monkeypatch.setattr(mcp_server, "get_universe", lambda *args, **kwargs: ["sh.600487"])

    result = json.loads(await mcp_server.compute_factor_values(
        "close",
        universe="csi500",
        start_date="2024-01-02",
        end_date="2024-01-31",
        universe_date="2024-01-15",
        allow_remote_fetch=True,
    ))

    assert result["universe_date"] == "2024-01-15"
    assert calls == {"universe_date": "2024-01-15", "allow_remote_fetch": True}
