"""Strategy report wrapper tests."""

import json

from quantgpt.strategy.backtest import StrategyBacktestRequest, run_strategy_backtest
from quantgpt.strategy.report import generate_strategy_report
from quantgpt.strategy.spec import example_strategy_spec

from .test_strategy_backtest import _market_df


def test_generate_strategy_report_writes_summary_json(tmp_path, monkeypatch):
    spec = example_strategy_spec()
    spec["universe"] = "small_scale"
    spec["factors"][0]["expression"] = "close"
    spec["signal_rules"]["long_quantile"] = 0.5
    spec["risk_rules"]["max_asset_weight"] = 1.0
    spec["risk_rules"]["max_turnover"] = None
    spec["cost_model"]["bps"] = 0
    result = run_strategy_backtest(
        StrategyBacktestRequest.model_validate({
            "spec": spec,
            "start_date": "2024-01-02",
            "end_date": "2024-02-02",
        }),
        market_df=_market_df(),
    )

    def fake_generate_report(*args, **kwargs):
        report_path = tmp_path / "strategy.html"
        report_path.write_text("<html></html>", encoding="utf-8")
        return {"report_path": str(report_path), "metrics": {"total_return": 0.1}}

    monkeypatch.setattr("quantgpt.strategy.report.generate_report", fake_generate_report)

    report = generate_strategy_report(result, output_dir=str(tmp_path))
    summary = json.loads((tmp_path / "strategy.summary.json").read_text(encoding="utf-8"))

    assert report["report_path"].endswith("strategy.html")
    assert report["summary_json_path"].endswith("strategy.summary.json")
    assert summary["spec_version"] == "strategy_spec/v0"
    assert summary["latest_holdings"]
    assert summary["non_live_trading_notice"]
