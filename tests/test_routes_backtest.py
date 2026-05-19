"""Tests for backtest task routes — health, stats, task CRUD."""

import time
from concurrent.futures import Future

import pandas as pd
import pytest

from quantgpt.task_store import tasks, tasks_lock

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _clean_tasks():
    tasks.clear()
    yield
    tasks.clear()


class TestHealthEndpoint:
    async def test_returns_ok(self, client):
        resp = await client.get("/api/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "active_tasks" in data
        assert "auth_disabled" in data


class TestTaskStats:
    async def test_returns_stats(self, client, test_user, auth_headers):
        resp = await client.get("/api/v1/tasks/stats", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data
        assert "completed" in data
        assert "running" in data

    async def test_with_tasks_present(self, client, test_user, auth_headers):
        uid = str(test_user.id)
        with tasks_lock:
            tasks["t1"] = {"task_id": "t1", "user_id": uid, "status": "running", "created_at": time.time()}
            tasks["t2"] = {"task_id": "t2", "user_id": uid, "status": "completed", "created_at": time.time()}
        resp = await client.get("/api/v1/tasks/stats", headers=auth_headers)
        data = resp.json()
        assert data["total"] >= 1
        assert data["running"] >= 1


class TestListTasks:
    async def test_empty_list(self, client, test_user, auth_headers):
        resp = await client.get("/api/v1/tasks", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["tasks"] == []

    async def test_lists_user_tasks(self, client, test_user, auth_headers, db_session):
        from quantgpt.models import Task as TaskModel
        t = TaskModel(
            id="test-task-1",
            user_id=test_user.id,
            status="completed",
            task_type="backtest",
            expression="rank(close)",
        )
        db_session.add(t)
        await db_session.commit()

        resp = await client.get("/api/v1/tasks", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["tasks"]) == 1
        assert data["tasks"][0]["task_id"] == "test-task-1"


class TestGetTask:
    async def test_get_existing_task(self, client, test_user, auth_headers, db_session):
        from quantgpt.models import Task as TaskModel
        t = TaskModel(
            id="detail-task-1",
            user_id=test_user.id,
            status="completed",
            task_type="backtest",
            expression="rank(volume)",
            result={"score": 50},
        )
        db_session.add(t)
        await db_session.commit()

        resp = await client.get("/api/v1/tasks/detail-task-1", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == "detail-task-1"
        assert data["expression"] == "rank(volume)"

    async def test_get_nonexistent_task(self, client, test_user, auth_headers):
        resp = await client.get("/api/v1/tasks/nonexistent", headers=auth_headers)
        assert resp.status_code == 404


class TestCancelTask:
    async def test_cancel_running_task(self, client, test_user, auth_headers):
        uid = str(test_user.id)
        with tasks_lock:
            tasks["cancel-me"] = {
                "task_id": "cancel-me",
                "user_id": uid,
                "status": "running",
                "cancelled": False,
                "created_at": time.time(),
            }
        resp = await client.post("/api/v1/tasks/cancel-me/cancel", headers=auth_headers)
        assert resp.status_code == 200
        assert tasks["cancel-me"]["status"] == "cancelled"

    async def test_cancel_nonexistent_task(self, client, test_user, auth_headers):
        resp = await client.post("/api/v1/tasks/nonexistent/cancel", headers=auth_headers)
        assert resp.status_code == 404

    async def test_cancel_completed_task_fails(self, client, test_user, auth_headers):
        uid = str(test_user.id)
        with tasks_lock:
            tasks["done-task"] = {
                "task_id": "done-task",
                "user_id": uid,
                "status": "completed",
                "cancelled": False,
                "created_at": time.time(),
            }
        resp = await client.post("/api/v1/tasks/done-task/cancel", headers=auth_headers)
        assert resp.status_code == 400


class TestAutoBacktestValidation:
    async def test_empty_prompt_rejected(self, client, test_user, auth_headers):
        resp = await client.post("/api/v1/auto_backtest", json={
            "prompt": "",
        }, headers=auth_headers)
        assert resp.status_code == 422

    async def test_invalid_universe_rejected(self, client, test_user, auth_headers):
        resp = await client.post("/api/v1/auto_backtest", json={
            "prompt": "test factor",
            "universe": "invalid_universe",
        }, headers=auth_headers)
        assert resp.status_code == 422

    async def test_invalid_date_format_rejected(self, client, test_user, auth_headers):
        resp = await client.post("/api/v1/auto_backtest", json={
            "prompt": "test factor",
            "start_date": "01-01-2023",
        }, headers=auth_headers)
        assert resp.status_code == 422

    async def test_oos_rejects_external_fixed_direction(self, client, test_user, auth_headers):
        resp = await client.post("/api/v1/auto_backtest", json={
            "prompt": "close",
            "oos_enabled": True,
            "direction_mode": "fixed",
            "fixed_direction": 1,
        }, headers=auth_headers)
        assert resp.status_code == 422

    async def test_non_oos_validates_direction_combinations(self, client, test_user, auth_headers):
        resp = await client.post("/api/v1/auto_backtest", json={
            "prompt": "close",
            "direction_mode": "fixed",
        }, headers=auth_headers)
        assert resp.status_code == 422


def _route_market_df() -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-02", periods=10)
    rows = []
    for stock in ("A", "B", "C"):
        price = 10.0
        for date in dates:
            close = price * 1.01
            rows.append({
                "trade_date": date,
                "stock_code": stock,
                "open": price,
                "high": close * 1.01,
                "low": price * 0.99,
                "close": close,
                "volume": 1_000,
                "amount": close * 1_000,
                "pct_change": 1.0,
            })
            price = close
    return pd.DataFrame(rows)


def _route_backtest_result(oos: bool = False) -> dict:
    dates = pd.bdate_range("2024-01-02", periods=5)
    returns = pd.Series([0.01, 0.0, 0.02, -0.01, 0.01], index=dates)
    result = {
        "strategy_returns": returns,
        "ls_returns": returns,
        "long_short_sharpe": 1.2,
        "long_short_annual": 0.2,
        "top_group_sharpe": 1.0,
        "monotonicity_score": 0.8,
        "spread": 0.01,
        "group_returns": {0: {"mean_return": 0.0}},
        "ic_mean": 0.02,
        "rank_ic_mean": 0.02,
        "raw_ic_mean": 0.02,
        "raw_rank_ic_mean": 0.02,
        "direction_adjusted_ic_mean": 0.02,
        "direction_adjusted_rank_ic_mean": 0.02,
        "direction_mode": "auto_full",
        "direction_source": "auto_full_deprecated",
        "direction_basis": "cost_adjusted_group_mean",
        "fixed_direction": 1,
        "direction_warning": "compat",
        "flipped": False,
        "ic_ir": 0.3,
        "ic_win_rate": 0.6,
        "turnover": 0.1,
        "wq_fitness": 0.5,
        "cost_adjusted": True,
        "cost_rate": 0.003,
        "total_cost_drag": 0.0,
        "wq_brain": {},
        "_stock_factor_data": {"stocks": []},
    }
    if oos:
        result.update({
            "oos_result": {
                "direction_policy": "train_fixed",
                "report_scope": "oos_train_valid_test",
                "train": {"metrics": {"long_short_sharpe": 1.0}},
                "valid": {"metrics": {"long_short_sharpe": 0.8}},
                "test": {"metrics": {"long_short_sharpe": 0.5}},
                "_private": returns,
            },
            "direction_policy": "train_fixed",
            "report_scope": "legacy_compat_single_run",
            "compatibility_warning": "legacy",
        })
    return result


class _ImmediateThread:
    def __init__(self, target, args=(), daemon=None):
        self._target = target
        self._args = args

    def start(self):
        self._target(*self._args)


class _FakeFetcher:
    def fetch_stocks(self, stock_codes, start_date, end_date):
        return _route_market_df()


class _RouteFakeExecutor:
    def submit_cpu_work(self, fn, *args, **kwargs):
        future = Future()
        future.set_result(_route_backtest_result(oos=fn.__name__ == "_run_oos_backtest_in_process"))
        return future


@pytest.fixture
def auto_backtest_fakes(monkeypatch, tmp_path):
    import quantgpt.routes.backtest_tasks as route

    monkeypatch.setattr(route.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(route, "persist_task_to_db", lambda *args, **kwargs: None)
    monkeypatch.setattr(route, "_looks_like_expression", lambda prompt: True)
    monkeypatch.setattr(route, "get_universe", lambda universe, date=None: ["A", "B", "C"])
    monkeypatch.setattr(route, "MarketDataFetcher", _FakeFetcher)
    monkeypatch.setattr(route, "fetch_benchmark_returns", lambda *args, **kwargs: pd.Series(dtype=float))
    monkeypatch.setattr(route, "get_executor", lambda: _RouteFakeExecutor())
    monkeypatch.setattr(route, "generate_report", lambda *args, **kwargs: {
        "report_path": str(tmp_path / "backtest_report_test.html"),
        "metrics": {"sharpe": 1.0},
    })
    monkeypatch.setattr(route, "_call_interpret_factor", lambda **kwargs: {})
    monkeypatch.setattr(route, "compute_factor_score", lambda **kwargs: {
        "score": 80,
        "grade": "B",
        "component_scores": {},
    })


class TestAutoBacktestOOSResults:
    async def test_legacy_auto_backtest_response_and_result_shape(
        self, client, test_user, auth_headers, auto_backtest_fakes
    ):
        resp = await client.post("/api/v1/auto_backtest", json={
            "prompt": "close",
            "rebalance_anchor": "2024-01-02",
        }, headers=auth_headers)

        assert resp.status_code == 202
        assert set(resp.json()) == {"task_id", "status"}
        task = tasks[resp.json()["task_id"]]
        assert task["status"] == "completed"
        result = task["result"]
        for key in ("report_url", "metrics", "backtest_summary", "scoring", "interpretation", "stock_factor_data", "nav_series", "params", "llm"):
            assert key in result
        assert "oos_result" not in result
        assert "data_quality" not in result
        assert result["params"]["rebalance_anchor"] == "2024-01-02"

    async def test_oos_completed_task_result_is_public_and_marked(
        self, client, test_user, auth_headers, auto_backtest_fakes
    ):
        resp = await client.post("/api/v1/auto_backtest", json={
            "prompt": "close",
            "oos_enabled": True,
            "oos": {"min_train_days": 5, "min_valid_days": 5, "min_test_days": 5},
        }, headers=auth_headers)

        assert resp.status_code == 202
        result = tasks[resp.json()["task_id"]]["result"]
        assert result["direction_policy"] == "train_fixed"
        assert result["report_scope"] == "legacy_compat_single_run"
        assert result["data_quality"]["enabled"] is True
        assert result["oos_result"]["report_scope"] == "oos_train_valid_test"
        assert "_private" not in result["oos_result"]
        assert result["backtest_summary"]["metrics_scope"] == "legacy_compat_single_run"

    async def test_data_quality_only_non_oos_result_has_no_oos_result(
        self, client, test_user, auth_headers, auto_backtest_fakes
    ):
        resp = await client.post("/api/v1/auto_backtest", json={
            "prompt": "close",
            "data_quality": {"enabled": True, "adjustment": "qfq"},
        }, headers=auth_headers)

        assert resp.status_code == 202
        result = tasks[resp.json()["task_id"]]["result"]
        assert result["data_quality"]["enabled"] is True
        assert "oos_result" not in result
