"""Strategy REST route tests."""

import pytest

from quantgpt.strategy.spec import example_strategy_spec
from quantgpt.task_store import tasks

pytestmark = pytest.mark.asyncio


class ImmediateThread:
    def __init__(self, target, args=(), kwargs=None, daemon=None):
        self.target = target
        self.args = args
        self.kwargs = kwargs or {}
        self.daemon = daemon

    def start(self):
        self.target(*self.args, **self.kwargs)


async def test_strategy_markets_and_fields(client):
    markets = await client.get("/api/v1/strategy/markets")
    fields = await client.get("/api/v1/strategy/data-fields?market=a_share")

    assert markets.status_code == 200
    assert any(market["market"] == "a_share" for market in markets.json()["markets"])
    assert fields.status_code == 200
    assert any(field["name"] == "close" for field in fields.json()["data_fields"])


async def test_strategy_validate_rejects_invalid_spec(client):
    spec = example_strategy_spec()
    spec["market"] = "crypto"

    response = await client.post("/api/v1/strategy/validate", json={"spec": spec})

    assert response.status_code == 400
    assert response.json()["detail"]["is_valid"] is False


async def test_strategy_backtest_task_result_contains_score_report(client, monkeypatch):
    from quantgpt.routes import strategy as strategy_route

    spec = example_strategy_spec()
    payload = {
        "spec": spec,
        "start_date": "2024-01-02",
        "end_date": "2024-02-02",
        "benchmark": "hs300",
        "metrics": {"sharpe": 1.0},
        "risk_logs": [],
        "validation_issues": [],
        "latest_holdings": [],
        "strategy_score": {"score": 70, "grade": "B"},
    }
    monkeypatch.setattr(strategy_route.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(strategy_route, "_execute_strategy_backtest", lambda request_data, user_id: payload)
    monkeypatch.setattr(
        strategy_route,
        "_execute_strategy_report",
        lambda result_payload, user_id: {
            "report_path": "/tmp/backtest_report_strategy.html",
            "summary_json_path": "/tmp/backtest_report_strategy.summary.json",
        },
    )

    response = await client.post(
        "/api/v1/strategy/backtest",
        json={
            "spec": spec,
            "start_date": "2024-01-02",
            "end_date": "2024-02-02",
            "benchmark": "hs300",
        },
    )

    assert response.status_code == 202
    task = tasks[response.json()["task_id"]]
    assert task["status"] == "completed"
    assert task["task_type"] == "strategy_backtest"
    assert task["result"]["strategy_score"]["score"] == 70
    assert task["result"]["summary_json"].endswith(".summary.json")


async def test_strategy_post_mvp_result_endpoints(client):
    spec = example_strategy_spec()
    result = {
        "spec": spec,
        "start_date": "2024-01-02",
        "end_date": "2024-02-02",
        "benchmark": "hs300",
        "metrics": {"sharpe": 1.0, "annual_return": 0.1, "max_drawdown": -0.03, "turnover": 0.2, "ic_ir": 0.5},
        "risk_logs": [],
        "validation_issues": [],
        "diagnostics": {},
        "latest_holdings": [{"stock_code": "A", "target_weight": 0.5}],
        "strategy_returns": [{"date": "2024-01-03", "value": 0.01}],
        "target_weights": [{"trade_date": "2024-01-02", "stock_code": "A", "target_weight": 0.5}],
        "cash_weights": [{"trade_date": "2024-01-02", "cash_weight": 0.5}],
        "turnover_by_rebalance": [],
        "cost_by_rebalance": [],
    }

    export = await client.post("/api/v1/strategy/export", json={"result": result})
    diagnosis = await client.post("/api/v1/strategy/diagnose", json={"result": result})
    anti = await client.post("/api/v1/strategy/anti-overfit", json={"result": result})
    rolling = await client.post("/api/v1/strategy/rolling-validation", json={"result": result, "windows": 1})

    assert export.status_code == 200
    assert export.json()["signals"][0]["stock_code"] == "A"
    assert diagnosis.status_code == 200
    assert "diagnoses" in diagnosis.json()
    assert anti.status_code == 200
    assert anti.json()["type"] == "strategy_anti_overfit"
    assert rolling.status_code == 200
    assert rolling.json()["type"] == "strategy_rolling_validation"
