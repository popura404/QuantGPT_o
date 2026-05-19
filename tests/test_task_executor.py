"""Task executor wrapper regressions."""

import pickle

import pandas as pd
import pytest

import quantgpt.task_executor as task_executor
from quantgpt.validation.split import OOSConfig


def test_run_oos_backtest_in_process_is_top_level_and_registered():
    fn = task_executor._run_oos_backtest_in_process

    assert fn.__module__ == "quantgpt.task_executor"
    assert pickle.loads(pickle.dumps(fn)) is fn
    assert task_executor.CeleryTaskExecutor._FN_PATHS[fn] == "quantgpt.task_executor._run_oos_backtest_in_process"


def test_run_oos_backtest_in_process_wraps_api_context(monkeypatch):
    calls = []

    def fake_enable():
        calls.append("enable")

    def fake_disable():
        calls.append("disable")

    def fake_oos(market_df, expression, n_groups, holding_period, **kwargs):
        calls.append((expression, n_groups, holding_period, kwargs["oos_config"].min_train_days))
        return {"ok": True}

    monkeypatch.setattr("quantgpt.backtest.enable_api_context", fake_enable)
    monkeypatch.setattr("quantgpt.backtest.disable_api_context", fake_disable)
    monkeypatch.setattr("quantgpt.validation.oos_backtest.run_factor_oos_backtest", fake_oos)

    result = task_executor._run_oos_backtest_in_process(
        pd.DataFrame({"trade_date": []}),
        "close",
        3,
        5,
        oos_config=OOSConfig(min_train_days=5, min_valid_days=5, min_test_days=5),
    )

    assert result == {"ok": True}
    assert calls[0] == "enable"
    assert calls[-1] == "disable"
    assert calls[1] == ("close", 3, 5, 5)


def test_run_oos_backtest_in_process_disables_context_on_failure(monkeypatch):
    calls = []

    monkeypatch.setattr("quantgpt.backtest.enable_api_context", lambda: calls.append("enable"))
    monkeypatch.setattr("quantgpt.backtest.disable_api_context", lambda: calls.append("disable"))

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("quantgpt.validation.oos_backtest.run_factor_oos_backtest", boom)

    with pytest.raises(RuntimeError, match="boom"):
        task_executor._run_oos_backtest_in_process(pd.DataFrame(), "close", 3, 5)

    assert calls == ["enable", "disable"]
