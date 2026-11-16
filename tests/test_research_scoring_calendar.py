"""Registered strategy scoring windows, per-asset history and final risk evidence."""

import pandas as pd

from quantgpt.research.contracts import EvaluationConfigV1
from quantgpt.research.demo import prepare_offline_demo
from quantgpt.research.evaluations import load_evaluation_panel
from quantgpt.research.validation_runner import _numerical_checks, _replay


def fixture(root, *, start: int, end: int, min_history: int, expression="rank(close)"):
    demo = prepare_offline_demo(root)
    dates = pd.bdate_range("2024-01-02", periods=180)
    config = EvaluationConfigV1.model_validate({**demo["config"], "window": {
        **demo["config"]["window"], "phase": "final", "start_session": dates[start].date(),
        "end_session": dates[end].date()}})
    spec = demo["strategy_template"]
    spec["validation"]["min_history_days"] = min_history
    spec["factors"][0]["expression"] = expression
    spec["factor_evaluations"] = [{"evaluation_id": "fixture", "evaluation_hash": "a" * 64,
        "definition_hash": "b" * 64, "project_id": "fixture", "backend": "local", "scope": "selection"}]
    return dates, config, {"strategy": spec}, load_evaluation_panel(config, root)


def test_scoring_window_is_fixed_during_long_warmup_and_per_security_history(tmp_path):
    dates, config, frozen, frame = fixture(tmp_path, start=40, end=110, min_history=65,
                                            expression="rank(ts_mean(close, 80))")
    # One security starts trading well after the rest. It cannot borrow the other
    # securities' histories, even when every valid security is selected.
    late = frame.stock_code.iloc[0]
    frame = frame.loc[(frame.stock_code != late) | (pd.to_datetime(frame.trade_date) >= dates[35])].copy()
    frozen["strategy"]["signal_rules"]["top_n"] = 20
    replay = _replay(frame, frozen, config)
    assert replay["returns"].index.tolist() == dates[40:111].tolist()
    assert replay["returns"].loc[:dates[79]].eq(0).all()
    assert replay["simulation"]["invested_sessions"] > 0
    assert replay["weights"].trade_date.min() == dates[80]
    assert late not in set(replay["weights"].stock_code)
    # A short-expression model is also gated by its own configured minimum.
    frozen["strategy"]["factors"][0]["expression"] = "rank(close)"
    short = _replay(frame, frozen, config)
    assert short["returns"].index.equals(replay["returns"].index)
    assert short["weights"].trade_date.min() == dates[65]
    assert short["weights"].loc[short["weights"].stock_code == late, "trade_date"].min() == dates[100]
    # A later fresh-cash fold retains pre-fold feature history and only publishes
    # decisions from the fold. It does not restart the observation count.
    fold = _replay(frame, frozen, config, dates[90].date().isoformat(), dates[109].date().isoformat())
    assert fold["returns"].index.tolist() == dates[90:110].tolist()
    assert fold["weights"].trade_date.min() == dates[90]


def test_final_risk_has_no_credit_from_warmup_targets_or_cash_only_windows(tmp_path):
    dates, config, frozen, frame = fixture(tmp_path, start=100, end=179, min_history=30)
    frame["_research_eligible"] = pd.to_datetime(frame.trade_date) < dates[100]
    result = _replay(frame, frozen, config)
    assert result["weights"].empty
    assert result["returns"].index.tolist() == dates[100:].tolist() and result["returns"].eq(0).all()
    checks = _numerical_checks(frame, frozen, config, result["returns"], 8)
    assert checks["simulation"]["status"] == checks["label_boundaries"]["status"] == "passed"
    assert checks["risk"]["status"] == "insufficient_data"
    assert checks["risk"]["window_target_rows"] == checks["risk"]["invested_sessions"] == 0
    assert [fold["sessions"] for fold in checks["walk_forward"]["windows"]] == [27, 27, 26]
