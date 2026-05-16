"""MCP strategy tool tests."""

import json

import pytest

from quantgpt import mcp_server
from quantgpt.strategy.spec import example_strategy_spec


def test_mcp_strategy_discovery_tools_return_json():
    markets = json.loads(mcp_server.list_markets())
    fields = json.loads(mcp_server.list_data_fields("a_share"))
    templates = json.loads(mcp_server.list_strategy_templates())

    assert any(market["market"] == "a_share" for market in markets["markets"])
    assert any(field["name"] == "close" for field in fields["data_fields"])
    assert any(template["id"] == "momentum_top_n_v1" for template in templates["templates"])


def test_mcp_validate_strategy_spec_returns_structured_result():
    result = json.loads(mcp_server.validate_strategy_spec(example_strategy_spec()))

    assert result["is_valid"] is True


@pytest.mark.asyncio
async def test_mcp_run_score_report_strategy_flow(monkeypatch, tmp_path):
    spec = example_strategy_spec()
    payload = {
        "spec": spec,
        "start_date": "2024-01-02",
        "end_date": "2024-02-02",
        "benchmark": "hs300",
        "metrics": {"sharpe": 1.0, "annual_return": 0.1, "max_drawdown": -0.03, "turnover": 0.2, "ic_ir": 0.5},
        "risk_logs": [],
        "validation_issues": [],
        "latest_holdings": [{"stock_code": "A", "target_weight": 0.5}],
        "strategy_returns": [{"date": "2024-01-03", "value": 0.01}],
        "target_weights": [{"trade_date": "2024-01-02", "stock_code": "A", "target_weight": 0.5}],
        "cash_weights": [{"trade_date": "2024-01-02", "cash_weight": 0.5}],
        "turnover_by_rebalance": [],
        "cost_by_rebalance": [],
    }

    monkeypatch.setattr(mcp_server, "_run_strategy_backtest_payload", lambda request: payload)
    monkeypatch.setattr(
        mcp_server,
        "_generate_strategy_report_payload",
        lambda result: {"report_path": str(tmp_path / "report.html"), "summary_json_path": str(tmp_path / "summary.json")},
    )

    backtest = json.loads(await mcp_server.run_strategy_backtest(spec, "2024-01-02", "2024-02-02"))
    score = json.loads(mcp_server.score_strategy(backtest))
    report = json.loads(await mcp_server.generate_strategy_report(backtest))
    export = json.loads(mcp_server.export_strategy_candidate(backtest))
    diagnosis = json.loads(mcp_server.diagnose_strategy(backtest))
    anti = json.loads(mcp_server.run_strategy_anti_overfit(backtest))
    rolling = json.loads(mcp_server.run_strategy_rolling_validation(backtest))
    optimized = json.loads(mcp_server.optimize_strategy_candidate(
        [{"trade_date": "2024-01-02", "stock_code": "A", "score": 1.0}],
        spec,
    ))

    assert backtest["latest_holdings"][0]["stock_code"] == "A"
    assert 0 <= score["score"] <= 100
    assert report["summary_json_path"].endswith("summary.json")
    assert export["signals"][0]["stock_code"] == "A"
    assert "diagnoses" in diagnosis
    assert anti["type"] == "strategy_anti_overfit"
    assert rolling["type"] == "strategy_rolling_validation"
    assert optimized["target_weights"][0]["stock_code"] == "A"
