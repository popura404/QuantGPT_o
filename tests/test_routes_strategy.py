"""Strategy REST route tests."""

import pytest

from quantgpt.auth import create_guest_token
from quantgpt.strategy.spec import example_strategy_spec
from quantgpt.task_store import tasks
from quantgpt.validation.promotion import build_factor_validation_provenance

pytestmark = pytest.mark.asyncio


def _validation_provenance() -> dict:
    return build_factor_validation_provenance(
        oos_result={
            "direction_policy": "train_fixed",
            "train": {"metrics": {"long_short_sharpe": 1.0, "direction_adjusted_rank_ic_mean": 0.03}},
            "valid": {"metrics": {"long_short_sharpe": 0.9, "direction_adjusted_rank_ic_mean": 0.02}},
            "test": {"metrics": {"long_short_sharpe": 0.8, "direction_adjusted_rank_ic_mean": 0.02}},
        },
        oos_score={"decision": "candidate", "score": 80},
        data_quality={"enabled": True, "after_rows": 100, "after_stocks": 10},
        rolling_validation={"score": 70, "windows": [{"window_index": 0}]},
        placebo_test={"passed": True, "details": {"perm_pass": True, "decay_ok": True, "shift_ics": {"5": 0.01}}},
    )


@pytest.fixture(autouse=True)
def _clean_tasks():
    tasks.clear()
    yield
    tasks.clear()


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
    templates = await client.get("/api/v1/strategy/templates")

    assert markets.status_code == 200
    assert any(market["market"] == "a_share" for market in markets.json()["markets"])
    assert fields.status_code == 200
    assert any(field["name"] == "close" for field in fields.json()["data_fields"])
    assert templates.status_code == 200
    assert any(template["id"] == "momentum_top_n_v1" for template in templates.json()["templates"])


async def test_strategy_validate_rejects_invalid_spec(client):
    spec = example_strategy_spec()
    spec["market"] = "crypto"

    response = await client.post("/api/v1/strategy/validate", json={"spec": spec})

    assert response.status_code == 400
    assert response.json()["detail"]["is_valid"] is False


async def test_strategy_backtest_task_result_contains_score_report(client, monkeypatch, test_user, auth_headers):
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
    monkeypatch.setattr(strategy_route, "persist_task_to_db", lambda *args, **kwargs: None)
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
        headers=auth_headers,
    )

    assert response.status_code == 202
    task = tasks[response.json()["task_id"]]
    assert task["status"] == "completed"
    assert task["task_type"] == "strategy_backtest"
    assert task["user_id"] == str(test_user.id)
    assert task["result"]["strategy_score"]["score"] == 70
    assert task["result"]["summary_json"] == "/tmp/backtest_report_strategy.summary.json"

    detail = await client.get(f"/api/v1/tasks/{response.json()['task_id']}", headers=auth_headers)
    assert detail.status_code == 200
    assert detail.json()["result"]["report_url"] == "/api/v1/reports/backtest_report_strategy.html"


async def test_strategy_report_url_reads_owned_report(client, monkeypatch, test_user, auth_headers):
    from quantgpt.routes import strategy as strategy_route
    from quantgpt.routes.iteration_routes import REPORT_DIR

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
    report_dir = REPORT_DIR / str(test_user.id)
    report_dir.mkdir(parents=True, exist_ok=True)
    report_file = report_dir / "backtest_report_strategy.html"
    report_file.write_text("<html>strategy report</html>", encoding="utf-8")

    monkeypatch.setattr(strategy_route.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(strategy_route, "_execute_strategy_backtest", lambda request_data, user_id: payload)
    monkeypatch.setattr(strategy_route, "persist_task_to_db", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        strategy_route,
        "_execute_strategy_report",
        lambda result_payload, user_id: {
            "report_path": str(report_file),
            "summary_json_path": str(report_file.with_suffix(".summary.json")),
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
        headers=auth_headers,
    )

    report_url = tasks[response.json()["task_id"]]["result"]["report_url"]
    report = await client.get(report_url, headers=auth_headers)

    assert report.status_code == 200
    assert "strategy report" in report.text


async def test_strategy_post_mvp_result_endpoints(client, auth_headers):
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
        "validation_provenance": _validation_provenance(),
    }

    export = await client.post("/api/v1/strategy/export", json={"result": result}, headers=auth_headers)
    diagnosis = await client.post("/api/v1/strategy/diagnose", json={"result": result}, headers=auth_headers)
    anti = await client.post("/api/v1/strategy/anti-overfit", json={"result": result}, headers=auth_headers)
    rolling = await client.post(
        "/api/v1/strategy/rolling-validation",
        json={"result": result, "windows": 1},
        headers=auth_headers,
    )

    assert export.status_code == 200
    assert export.json()["signals"][0]["stock_code"] == "A"
    assert diagnosis.status_code == 200
    assert "diagnoses" in diagnosis.json()
    assert anti.status_code == 200
    assert anti.json()["type"] == "strategy_anti_overfit"
    assert rolling.status_code == 200
    assert rolling.json()["type"] == "strategy_rolling_validation"


async def test_strategy_backtest_rejects_anonymous_without_creating_task(client):
    spec = example_strategy_spec()

    response = await client.post(
        "/api/v1/strategy/backtest",
        json={
            "spec": spec,
            "start_date": "2024-01-02",
            "end_date": "2024-02-02",
            "benchmark": "hs300",
        },
    )

    assert response.status_code == 401
    assert tasks == {}


async def test_strategy_backtest_rejects_guest_token_without_creating_task(client):
    spec = example_strategy_spec()
    guest_headers = {"Authorization": f"Bearer {create_guest_token()}"}

    response = await client.post(
        "/api/v1/strategy/backtest",
        json={
            "spec": spec,
            "start_date": "2024-01-02",
            "end_date": "2024-02-02",
            "benchmark": "hs300",
        },
        headers=guest_headers,
    )

    assert response.status_code == 401
    assert tasks == {}


async def test_strategy_backtest_auth_disabled_allows_dev_user(client, monkeypatch):
    from quantgpt.routes import strategy as strategy_route

    monkeypatch.setenv("AUTH_DISABLED", "true")
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
    monkeypatch.setattr(strategy_route, "persist_task_to_db", lambda *args, **kwargs: None)
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
    assert task["user_id"] == "00000000-0000-0000-0000-000000000099"
    assert task["status"] == "completed"


async def test_strategy_sync_result_endpoints_reject_anonymous(client):
    spec = example_strategy_spec()
    result = {
        "spec": spec,
        "strategy_returns": [{"date": "2024-01-03", "value": 0.01}],
        "target_weights": [{"trade_date": "2024-01-02", "stock_code": "A", "target_weight": 0.5}],
        "validation_provenance": _validation_provenance(),
    }

    endpoints = [
        ("/api/v1/strategy/export", {"result": result}),
        ("/api/v1/strategy/diagnose", {"result": result}),
        ("/api/v1/strategy/anti-overfit", {"result": result}),
        ("/api/v1/strategy/rolling-validation", {"result": result, "windows": 1}),
        (
            "/api/v1/strategy/optimize",
            {
                "spec": spec,
                "signals": [{"trade_date": "2024-01-02", "stock_code": "A", "score": 1.0}],
            },
        ),
    ]

    for path, payload in endpoints:
        response = await client.post(path, json=payload)
        assert response.status_code == 401


async def test_strategy_result_endpoint_rejects_oversized_payload(client, auth_headers):
    response = await client.post(
        "/api/v1/strategy/diagnose",
        json={
            "result": {
                "strategy_returns": [
                    {"date": "2024-01-03", "value": 0.01}
                    for _ in range(10_001)
                ]
            }
        },
        headers=auth_headers,
    )

    assert response.status_code == 422


async def test_strategy_template_and_optimizer_endpoints(client, auth_headers):
    template = await client.post(
        "/api/v1/strategy/templates/momentum_top_n_v1/instantiate",
        json={"overrides": {"signal_rules.top_n": 3, "risk_rules.max_asset_weight": 0.4}},
    )
    assert template.status_code == 200
    spec = template.json()["spec"]
    assert template.json()["validation"]["is_valid"] is True

    optimized = await client.post(
        "/api/v1/strategy/optimize",
        json={
            "spec": spec,
            "signals": [
                {"trade_date": "2024-01-02", "stock_code": "A", "score": 10.0},
                {"trade_date": "2024-01-02", "stock_code": "B", "score": 1.0},
            ],
        },
        headers=auth_headers,
    )
    assert optimized.status_code == 200
    assert max(row["target_weight"] for row in optimized.json()["target_weights"]) <= 0.4
