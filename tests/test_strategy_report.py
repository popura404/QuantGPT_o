"""Strategy report wrapper tests."""

import json
from pathlib import Path

import pandas as pd

from quantgpt.report import generate_report
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


def test_generate_report_escapes_html_title(tmp_path, monkeypatch):
    import quantstats as qs

    captured = {}

    def fake_html(*args, **kwargs):
        captured["title"] = kwargs["title"]
        Path(kwargs["output"]).write_text(
            f"<html><head></head><body><h1>{kwargs['title']}</h1></body></html>",
            encoding="utf-8",
        )

    monkeypatch.setattr(qs.reports, "html", fake_html)
    for name in ("comp", "cagr", "sharpe", "sortino", "max_drawdown", "volatility", "win_rate", "profit_factor"):
        monkeypatch.setattr(qs.stats, name, lambda *args, **kwargs: 0.0)

    returns = pd.Series([0.01, 0.02, -0.01], index=pd.date_range("2024-01-01", periods=3))
    report = generate_report(
        returns,
        title='Unsafe</h1><script>window.__QGPT_XSS=1</script>',
        output_dir=str(tmp_path),
    )

    assert "<script>" not in captured["title"]
    assert "&lt;script&gt;" in captured["title"]
    assert "<script>" not in Path(report["report_path"]).read_text(encoding="utf-8")
