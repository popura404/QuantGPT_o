"""Tests for shared factor values service and REST route."""

import numpy as np
import pandas as pd
import pytest

from quantgpt import factor_values


def _market_frame():
    return pd.DataFrame({
        "trade_date": pd.to_datetime([
            "2024-01-01",
            "2024-01-02",
            "2024-01-02",
            "2024-01-03",
        ]),
        "stock_code": ["A", "A", "B", "A"],
        "close": [10.0, 11.0, 12.0, 13.0],
    })


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"expression": ""}, "expression must not be empty"),
        ({"expression": "x", "universe": "crypto"}, "universe must be one of"),
        ({"expression": "x", "start_date": "2024/01/01"}, "start_date must be YYYY-MM-DD format"),
        ({"expression": "x", "start_date": "2024-01-10", "end_date": "2024-01-01"}, "start_date must be"),
        ({"expression": "x", "start_date": "2020-01-01", "end_date": "2024-01-01"}, "Date range too large"),
    ],
)
def test_validate_factor_values_request_rejects_invalid_inputs(kwargs, match):
    params = {"universe": "csi500", **kwargs}
    with pytest.raises(ValueError, match=match):
        factor_values.validate_factor_values_request(**params)


def test_compute_factor_values_payload_groups_finite_values(monkeypatch):
    calls = {}

    def fake_get_universe(universe, date=None):
        calls["universe"] = universe
        calls["universe_date"] = date
        return ["A", "B"]

    class FakeFetcher:
        def fetch_stocks(self, stocks, start_date, end_date):
            calls["stocks"] = stocks
            calls["fetch_start"] = start_date
            calls["end_date"] = end_date
            return _market_frame()

    values = pd.Series([np.nan, 1.2345678, np.inf, -2.0])

    def fake_compute(market_df, expression=None, precomputed_factor=None):
        calls["expression"] = expression
        return values.reindex(market_df.index)

    monkeypatch.setattr(factor_values, "get_universe", fake_get_universe)
    monkeypatch.setattr(factor_values, "MarketDataFetcher", FakeFetcher)
    monkeypatch.setattr(factor_values, "compute_backtest_factor_values", fake_compute)

    payload = factor_values.compute_factor_values_payload(
        " close ",
        universe="csi500",
        start_date="2024-01-02",
        end_date="2024-01-03",
    )

    assert calls["universe"] == "csi500"
    assert calls["universe_date"] == "2024-01-02"
    assert calls["fetch_start"] < "2024-01-02"
    assert calls["end_date"] == "2024-01-03"
    assert calls["expression"] == "close"
    assert payload["trading_days"] == 2
    assert payload["data"][0] == {"date": "2024-01-02", "values": {"A": 1.234568}, "count": 1}
    assert payload["data"][1] == {"date": "2024-01-03", "values": {"A": -2.0}, "count": 1}


@pytest.mark.asyncio
async def test_factor_values_route_requires_auth(client):
    response = await client.post("/api/v1/factor_values", json={"expression": "close"})

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_factor_values_route_uses_shared_payload(client, auth_headers, monkeypatch):
    from quantgpt.routes import factor_values as route

    def fake_payload(expression, universe="csi500", start_date="", end_date=""):
        return {
            "expression": expression,
            "universe": universe,
            "start_date": start_date,
            "end_date": end_date,
            "trading_days": 1,
            "data": [{"date": "2024-01-02", "values": {"A": 1.0}, "count": 1}],
        }

    monkeypatch.setattr(route, "compute_factor_values_payload", fake_payload)

    response = await client.post(
        "/api/v1/factor_values",
        headers=auth_headers,
        json={
            "expression": "close",
            "universe": "small_scale",
            "start_date": "2024-01-02",
            "end_date": "2024-01-02",
        },
    )

    assert response.status_code == 200
    assert response.json()["data"][0]["values"] == {"A": 1.0}
