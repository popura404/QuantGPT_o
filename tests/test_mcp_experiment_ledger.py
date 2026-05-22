"""MCP experiment-ledger integration tests."""

import asyncio
import json
from concurrent.futures import Future

import pandas as pd
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from quantgpt import mcp_server
from quantgpt.models import Experiment, ExperimentArtifact, ExperimentResult, ExportEvent, PromotionEvent
from quantgpt.strategy.spec import example_strategy_spec_v1
from quantgpt.validation.promotion import build_factor_validation_provenance


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
        "_factor_df": _factor_df(),
    }


def _oos_result() -> dict:
    result = _legacy_result()
    result.update({
        "oos_result": {
            "direction_policy": "train_fixed",
            "report_scope": "oos_train_valid_test",
            "train": {"period": ["2024-01-02", "2024-01-03"], "metrics": {"long_short_sharpe": 1.0}},
            "valid": {"period": ["2024-01-04", "2024-01-05"], "metrics": {"long_short_sharpe": 0.7}},
            "test": {"period": ["2024-01-08", "2024-01-09"], "metrics": {"long_short_sharpe": 0.5}},
            "decay": {"test_sharpe_decay": 0.5},
        },
        "direction_policy": "train_fixed",
        "report_scope": "legacy_compat_single_run",
        "compatibility_warning": "legacy",
    })
    return result


def _factor_df() -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-02", periods=45)
    rows = []
    for idx, stock in enumerate(["A", "B", "C", "D", "E"]):
        for step, date in enumerate(dates):
            rows.append({
                "trade_date": date,
                "stock_code": stock,
                "factor_value": float(idx + step / 100),
                "daily_ret": 0.001 * (idx + 1),
            })
    return pd.DataFrame(rows)


class _FakeExecutor:
    def submit_cpu_work(self, fn, *args, **kwargs):
        fut = Future()
        fut.set_result(_oos_result() if fn.__name__ == "_run_oos_backtest_in_process" else _legacy_result())
        return fut


@pytest.fixture
def mcp_ledger_fakes(monkeypatch, tmp_path, engine):
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def fake_start(*args, **kwargs):
        return "task-1"

    async def fake_complete(*args, **kwargs):
        return None

    monkeypatch.setattr(mcp_server, "_get_ledger_session_factory", lambda: factory)
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

    import quantgpt.anti_overfit as anti_overfit
    import quantgpt.rolling_validator as rolling_validator

    monkeypatch.setattr(anti_overfit, "run_anti_overfit", lambda *args, **kwargs: {
        "score": 80,
        "recommendation": "ok",
        "tests": [],
    })
    monkeypatch.setattr(rolling_validator, "run_rolling_validation", lambda *args, **kwargs: {
        "score": 75,
        "windows": [{"window_index": 0}],
        "summary": {"n_windows": 1},
    })
    return factory


@pytest.mark.asyncio
async def test_mcp_run_backtest_creates_experiment_result_and_artifact(mcp_ledger_fakes):
    result = json.loads(await mcp_server.run_backtest("rank(close)", universe="small_scale", validation_stage="final"))

    async with mcp_ledger_fakes() as session:
        experiment = (
            await session.execute(select(Experiment).where(Experiment.experiment_id == result["experiment_id"]))
        ).scalar_one()
        result_row = (
            await session.execute(select(ExperimentResult).where(ExperimentResult.experiment_id == result["experiment_id"]))
        ).scalar_one()
        artifact = (
            await session.execute(select(ExperimentArtifact).where(ExperimentArtifact.experiment_id == result["experiment_id"]))
        ).scalar_one()

    assert result["factor_hash"] == experiment.factor_hash
    assert experiment.status == "validated_oos"
    assert experiment.task_id == "task-1"
    assert experiment.direction_policy == "train_fixed"
    assert experiment.data_snapshot_id == result["data_snapshot_id"]
    assert result_row.stage == "validated_oos"
    assert result_row.test_period == ["2024-01-08", "2024-01-09"]
    assert artifact.artifact_type == "report"


@pytest.mark.asyncio
async def test_mcp_score_factor_creates_experiment_and_query_tools(mcp_ledger_fakes):
    result = json.loads(await mcp_server.score_factor("rank(close)", universe="small_scale"))
    listed = json.loads(await mcp_server.list_experiments(factor_hash=result["factor_hash"]))
    detail = json.loads(await mcp_server.get_experiment(result["experiment_id"]))
    counts = json.loads(await mcp_server.summarize_trial_counts(universe="small_scale", factor_hash=result["factor_hash"]))

    assert listed["experiments"][0]["experiment_id"] == result["experiment_id"]
    assert detail["experiment_id"] == result["experiment_id"]
    assert detail["results"][0]["stage"] == "validated_oos"
    assert counts["trials_by_factor_hash"] == 1


@pytest.mark.asyncio
async def test_validate_expression_records_parse_failures(mcp_ledger_fakes):
    message = await mcp_server.validate_expression("rank(close")

    async with mcp_ledger_fakes() as session:
        experiment = (await session.execute(select(Experiment))).scalar_one()

    assert message.startswith("ERROR")
    assert experiment.status == "parse_failed"
    assert "括号不平衡" in str(experiment.failure_reason)


@pytest.mark.asyncio
async def test_promote_and_reject_experiment_record_events(mcp_ledger_fakes):
    result = json.loads(await mcp_server.score_factor("rank(close)", universe="small_scale", validation_stage="final"))
    provenance = build_factor_validation_provenance(
        oos_result={
            "direction_policy": "train_fixed",
            "train": {"metrics": {"long_short_sharpe": 1.1}},
            "valid": {"metrics": {"long_short_sharpe": 0.9}},
            "test": {"metrics": {"long_short_sharpe": 0.8}},
        },
        oos_score={"decision": "candidate", "score": 80, "grade": "A"},
        data_quality={"enabled": True, "after_rows": 100, "after_stocks": 10, "data_snapshot_id": result["data_snapshot_id"]},
        rolling_validation={"score": 70, "windows": [{"window_index": 0}]},
        placebo_test={"passed": True, "details": {"perm_pass": True, "decay_ok": True, "shift_ics": {"5": 0.01}}},
    )

    promote = json.loads(await mcp_server.promote_experiment(result["experiment_id"], provenance=provenance))
    rejected = json.loads(await mcp_server.reject_experiment(result["experiment_id"], "manual rejection"))

    async with mcp_ledger_fakes() as session:
        events = (
            await session.execute(select(PromotionEvent).where(PromotionEvent.experiment_id == result["experiment_id"]))
        ).scalars().all()

    assert promote["allowed"] is True
    assert rejected["status"] == "rejected"
    assert [event.decision for event in events] == ["allowed", "rejected"]


@pytest.mark.asyncio
async def test_run_multiple_testing_check_can_write_ledger_result(mcp_ledger_fakes):
    result = json.loads(await mcp_server.score_factor("rank(close)", universe="small_scale", validation_stage="final"))
    report = json.loads(await mcp_server.run_multiple_testing_check(
        p_value=0.001,
        trial_counts={"total_trials_in_project": 2, "trials_in_same_factor_family": 1},
        family_p_values=[0.001],
        experiment_id=result["experiment_id"],
    ))

    async with mcp_ledger_fakes() as session:
        experiment = (
            await session.execute(select(Experiment).where(Experiment.experiment_id == result["experiment_id"]))
        ).scalar_one()
        result_rows = (
            await session.execute(select(ExperimentResult).where(ExperimentResult.experiment_id == result["experiment_id"]))
        ).scalars().all()

    assert report["passed"] is True
    assert experiment.status == "multiple_testing_checked"
    assert any(row.stage == "multiple_testing_checked" for row in result_rows)


@pytest.mark.asyncio
async def test_auxiliary_factor_tools_create_ledger_rows(mcp_ledger_fakes):
    diagnosis = json.loads(await asyncio.to_thread(
        mcp_server.diagnose_factor,
        "rank(close)",
        0.03,
        0.4,
        0.8,
        75,
    ))
    anti = json.loads(await mcp_server.run_anti_overfit("rank(close)", universe="small_scale"))
    rolling = json.loads(await mcp_server.run_rolling_validation("rank(close)", universe="small_scale"))

    async with mcp_ledger_fakes() as session:
        experiments = (await session.execute(select(Experiment))).scalars().all()
        result_rows = (await session.execute(select(ExperimentResult))).scalars().all()
        artifacts = (await session.execute(select(ExperimentArtifact))).scalars().all()

    statuses = {row.status for row in experiments}
    stages = {row.stage for row in result_rows}
    artifact_types = {row.artifact_type for row in artifacts}

    assert diagnosis["experiment_id"].startswith("exp_")
    assert anti["experiment_id"].startswith("exp_")
    assert rolling["experiment_id"].startswith("exp_")
    assert {"parsed", "anti_overfit_checked", "rolling_checked"} <= statuses
    assert {"parsed", "anti_overfit_checked", "rolling_checked"} <= stages
    assert {"diagnosis", "anti_overfit", "rolling_validation"} <= artifact_types


@pytest.mark.asyncio
async def test_export_strategy_candidate_records_export_event(mcp_ledger_fakes):
    result = json.loads(await mcp_server.score_factor("rank(close)", universe="small_scale", validation_stage="final"))
    provenance = build_factor_validation_provenance(
        oos_result={
            "direction_policy": "train_fixed",
            "data_snapshot_id": result["data_snapshot_id"],
            "train": {"metrics": {"long_short_sharpe": 1.1}},
            "valid": {"metrics": {"long_short_sharpe": 0.9}},
            "test": {"metrics": {"long_short_sharpe": 0.8}},
        },
        oos_score={"decision": "candidate", "score": 80, "grade": "A"},
        data_quality={"enabled": True, "after_rows": 100, "after_stocks": 10, "data_snapshot_id": result["data_snapshot_id"]},
        rolling_validation={"score": 70, "windows": [{"window_index": 0}]},
        placebo_test={"passed": True, "details": {"perm_pass": True, "decay_ok": True, "shift_ics": {"5": 0.01}}},
    )
    promote = json.loads(await mcp_server.promote_experiment(result["experiment_id"], provenance=provenance))
    strategy_result = _strategy_export_input(result["experiment_id"], result["factor_hash"], result["data_snapshot_id"], provenance)

    export = json.loads(await asyncio.to_thread(mcp_server.export_strategy_candidate, strategy_result))

    async with mcp_ledger_fakes() as session:
        experiment = (
            await session.execute(select(Experiment).where(Experiment.experiment_id == result["experiment_id"]))
        ).scalar_one()
        export_event = (
            await session.execute(select(ExportEvent).where(ExportEvent.experiment_id == result["experiment_id"]))
        ).scalar_one()

    assert promote["allowed"] is True
    assert export["schema_version"] == "strategy_signal.v1"
    assert experiment.status == "exported"
    assert export_event.schema_version == "strategy_signal.v1"
    assert export_event.payload_hash is not None


@pytest.mark.asyncio
async def test_export_experiment_report_returns_detail(mcp_ledger_fakes):
    result = json.loads(await mcp_server.score_factor("rank(close)", universe="small_scale"))
    report = json.loads(await mcp_server.export_experiment_report(result["experiment_id"]))

    assert report["experiment"]["experiment_id"] == result["experiment_id"]
    assert "report_markdown" in report
    assert result["factor_hash"] in report["report_markdown"]


def _strategy_export_input(experiment_id: str, factor_hash: str, data_snapshot_id: str, provenance: dict) -> dict:
    return {
        "spec": example_strategy_spec_v1(),
        "start_date": "2024-01-02",
        "end_date": "2024-02-02",
        "benchmark": "hs300",
        "metrics": {"sharpe": 1.0, "annual_return": 0.1, "max_drawdown": -0.05, "turnover": 0.2},
        "risk_logs": [],
        "validation_issues": [],
        "diagnostics": {},
        "latest_holdings": [],
        "strategy_returns": [{"date": "2024-01-03", "value": 0.01}],
        "target_weights": [
            {"trade_date": "2024-01-02", "stock_code": "A", "target_weight": 0.6},
            {"trade_date": "2024-01-02", "stock_code": "B", "target_weight": 0.4},
        ],
        "cash_weights": [{"trade_date": "2024-01-02", "cash_weight": 0.0}],
        "turnover_by_rebalance": [],
        "cost_by_rebalance": [],
        "experiment_id": experiment_id,
        "factor_hash": factor_hash,
        "data_snapshot_id": data_snapshot_id,
        "direction_policy": "train_fixed",
        "oos_result": {
            "direction_policy": "train_fixed",
            "data_snapshot_id": data_snapshot_id,
            "train": {"period": ["2024-01-02", "2024-01-10"], "metrics": {"sharpe": 1.0}},
            "valid": {"period": ["2024-01-11", "2024-01-20"], "metrics": {"sharpe": 0.9}},
            "test": {"period": ["2024-01-21", "2024-02-02"], "metrics": {"sharpe": 0.8}},
        },
        "validation_provenance": provenance,
    }
