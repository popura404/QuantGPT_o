"""MCP factor backtest OOS/data-quality contract tests."""

import json
from concurrent.futures import Future

import pandas as pd
import pytest

from quantgpt import mcp_server


def _market_df() -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-02", periods=8)
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


def _legacy_result() -> dict:
    dates = pd.bdate_range("2024-01-02", periods=5)
    returns = pd.Series([0.01, 0.02, -0.01, 0.0, 0.01], index=dates)
    return {
        "strategy_returns": returns,
        "ls_returns": returns,
        "long_short_sharpe": 1.1,
        "long_short_annual": 0.2,
        "top_group_sharpe": 0.9,
        "monotonicity_score": 0.8,
        "spread": 0.01,
        "group_returns": {0: {"mean_return": 0.0}},
        "ic_mean": 0.03,
        "rank_ic_mean": 0.03,
        "raw_ic_mean": 0.03,
        "raw_rank_ic_mean": 0.03,
        "direction_adjusted_ic_mean": 0.03,
        "direction_adjusted_rank_ic_mean": 0.03,
        "direction_mode": "auto_full",
        "direction_source": "auto_full_deprecated",
        "direction_basis": "cost_adjusted_group_mean",
        "fixed_direction": 1,
        "direction_warning": "compat",
        "flipped": False,
        "ic_ir": 0.4,
        "ic_win_rate": 0.6,
        "turnover": 0.1,
        "wq_fitness": 0.5,
        "cost_adjusted": True,
        "cost_rate": 0.003,
        "total_cost_drag": 0.0,
    }


def _oos_result() -> dict:
    result = _legacy_result()
    result.update({
        "oos_result": {
            "direction_policy": "train_fixed",
            "report_scope": "oos_train_valid_test",
            "train": {
                "metrics": {
                    "long_short_sharpe": 1.0,
                    "direction_adjusted_rank_ic_mean": 0.04,
                    "ic_ir": 0.5,
                },
            },
            "valid": {
                "metrics": {
                    "long_short_sharpe": 0.7,
                    "direction_adjusted_rank_ic_mean": 0.03,
                    "ic_ir": 0.4,
                },
            },
            "test": {
                "metrics": {
                    "long_short_sharpe": 0.5,
                    "direction_adjusted_rank_ic_mean": 0.02,
                    "ic_ir": 0.3,
                    "turnover": 0.1,
                },
            },
            "decay": {"test_sharpe_decay": 0.5, "test_ic_decay": 0.5},
            "_private": pd.Series([1.0]),
        },
        "direction_policy": "train_fixed",
        "report_scope": "legacy_compat_single_run",
        "compatibility_warning": "legacy",
    })
    return result


class _FakeExecutor:
    def submit_cpu_work(self, fn, *args, **kwargs):
        fut = Future()
        if fn.__name__ == "_run_oos_backtest_in_process":
            fut.set_result(_oos_result())
        else:
            fut.set_result(_legacy_result())
        return fut


@pytest.fixture
def mcp_backtest_fakes(monkeypatch, tmp_path):
    async def fake_start(*args, **kwargs):
        return "task-1"

    async def fake_complete(*args, **kwargs):
        return None

    monkeypatch.setattr(mcp_server, "start_mcp_task", fake_start)
    monkeypatch.setattr(mcp_server, "complete_mcp_task", fake_complete)
    monkeypatch.setattr(mcp_server, "_fetch_data_for_market", lambda *args, **kwargs: (_market_df(), ["A", "B", "C"]))
    monkeypatch.setattr(mcp_server, "_enrich_with_fundamentals", lambda expression, market_df, *args: market_df)
    monkeypatch.setattr(mcp_server, "_fetch_benchmark_for_market", lambda *args, **kwargs: pd.Series(dtype=float))
    monkeypatch.setattr(mcp_server, "generate_report", lambda *args, **kwargs: {
        "report_path": str(tmp_path / "report.html"),
        "metrics": {"sharpe": 1.0},
    })
    monkeypatch.setattr(mcp_server, "get_executor", lambda: _FakeExecutor())


@pytest.mark.asyncio
async def test_legacy_mcp_run_backtest_omits_oos_and_data_quality_fields(mcp_backtest_fakes):
    result = json.loads(await mcp_server.run_backtest("close", universe="small_scale"))

    assert "oos_result" not in result
    assert "data_quality" not in result
    assert result["params"]["oos_enabled"] is False


@pytest.mark.asyncio
async def test_oos_mcp_run_backtest_returns_public_oos_and_data_quality(mcp_backtest_fakes):
    result = json.loads(await mcp_server.run_backtest("close", universe="small_scale", oos_enabled=True))

    assert result["direction_policy"] == "train_fixed"
    assert result["data_quality"]["enabled"] is True
    assert result["oos_result"]["report_scope"] == "oos_train_valid_test"
    assert "_private" not in result["oos_result"]
    assert result["backtest_summary"]["metrics_scope"] == "legacy_compat_single_run"


@pytest.mark.asyncio
async def test_oos_mcp_respects_explicit_data_quality_false(mcp_backtest_fakes):
    result = json.loads(await mcp_server.run_backtest(
        "close",
        universe="small_scale",
        oos_enabled=True,
        data_quality=False,
    ))

    assert result["data_quality"]["enabled"] is False
    assert result["oos_result"]["data_quality"]["enabled"] is False


@pytest.mark.asyncio
async def test_mcp_direction_validation_returns_structured_errors(mcp_backtest_fakes):
    oos_error = json.loads(await mcp_server.run_backtest(
        "close",
        oos_enabled=True,
        direction_mode="fixed",
        fixed_direction=1,
    ))
    non_oos_error = json.loads(await mcp_server.run_backtest(
        "close",
        direction_mode="fixed",
    ))

    assert oos_error["error_code"] == "INVALID_OOS_DIRECTION_OVERRIDE"
    assert non_oos_error["error_code"] == "INVALID_DIRECTION_POLICY"


@pytest.mark.asyncio
async def test_mcp_accepts_rebalance_anchor(mcp_backtest_fakes):
    result = json.loads(await mcp_server.run_backtest(
        "close",
        universe="small_scale",
        rebalance_anchor="2024-01-02",
    ))

    assert result["params"]["rebalance_anchor"] == "2024-01-02"


@pytest.mark.asyncio
async def test_legacy_score_factor_keeps_oos_and_data_quality_omitted(mcp_backtest_fakes):
    result = json.loads(await mcp_server.score_factor("close", universe="small_scale"))

    assert "oos_result" not in result
    assert "data_quality" not in result
    assert result["params"]["oos_enabled"] is False
    assert result["params"]["data_quality"] is None
    assert "score" in result
    assert "component_scores" in result


@pytest.mark.asyncio
async def test_oos_score_factor_returns_oos_first_score_and_data_quality(mcp_backtest_fakes):
    result = json.loads(await mcp_server.score_factor(
        "close",
        universe="small_scale",
        oos_enabled=True,
        rebalance_anchor="2024-01-02",
    ))

    assert result["direction_policy"] == "train_fixed"
    assert result["oos_score"]["metrics_scope"] == "oos_train_valid_test"
    assert result["data_quality"]["enabled"] is True
    assert result["oos_result"]["data_quality"]["enabled"] is True
    assert "_private" not in result["oos_result"]
    assert result["component_scores"]["test"] == result["oos_score"]["test_score"]
    assert result["params"]["oos_enabled"] is True
    assert result["params"]["rebalance_anchor"] == "2024-01-02"


@pytest.mark.asyncio
async def test_oos_score_factor_respects_explicit_data_quality_false(mcp_backtest_fakes):
    result = json.loads(await mcp_server.score_factor(
        "close",
        universe="small_scale",
        oos_enabled=True,
        data_quality=False,
    ))

    assert result["data_quality"]["enabled"] is False
    assert result["oos_result"]["data_quality"]["enabled"] is False


@pytest.mark.asyncio
async def test_score_factor_can_run_data_quality_without_oos(mcp_backtest_fakes):
    result = json.loads(await mcp_server.score_factor(
        "close",
        universe="small_scale",
        data_quality=True,
        adjustment="qfq",
    ))

    assert "oos_result" not in result
    assert result["data_quality"]["enabled"] is True
    assert result["params"]["oos_enabled"] is False
