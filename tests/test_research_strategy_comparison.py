import asyncio
import threading
import uuid
from copy import deepcopy

import numpy as np
import pandas as pd
import pytest

from quantgpt.research.comparison import _check_augmented, _paired_metrics


def _run(ids, weights):
    rules = dict.fromkeys(("signal_rules", "portfolio_rule", "cost_model", "risk_rules", "simulation_config", "validation"))
    return {"scope": "selection", "config": {"window": "same", "simulation_config": {"fees_bps": 10}},
            "spec": {**rules, "factors": [{"weight": weight} for weight in weights],
                     "factor_evaluations": [{"evaluation_id": key} for key in ids]}}


def test_augmented_comparison_enforces_common_costs_and_component_weights():
    baseline, augmented = _run(["a", "b"], [2, 4]), _run(["b", "a", "c"], [2, 1, 1])
    assert _check_augmented(baseline, augmented) == ["c"]
    for changed in (_run(["a", "c"], [1, 1]), _run(["a", "b", "c"], [2, 2, 1]),
                    _run(["a", "a", "b", "c"], [1, 1, 2, 1])):
        with pytest.raises(ValueError):
            _check_augmented(baseline, changed)
    changed = deepcopy(augmented)
    changed["config"]["simulation_config"]["fees_bps"] = 0
    with pytest.raises(ValueError, match="CONFIG_MISMATCH"):
        _check_augmented(baseline, changed)


def test_comparison_uses_paired_net_returns_and_reports_signal_overlap():
    dates = pd.bdate_range("2024-01-02", periods=40)
    baseline = pd.Series(np.linspace(-.005, .005, len(dates)), index=dates)
    augmented = baseline + .0003
    left = [{"trade_date": date, "stock_code": stock, "factor_value": idx}
            for date in dates for idx, stock in enumerate("ABCD")]
    right = [{**entry, "factor_value": -entry["factor_value"]} for entry in left if entry["stock_code"] != "D"]
    summary = _paired_metrics(baseline, augmented, (left, right), 3)
    assert summary["paired_mean_daily_net_return_difference"] == pytest.approx(.0003)
    assert summary["paired_mean_interval"]["lower"] == pytest.approx(.0003)
    assert summary["net_total_return"]["difference"] == pytest.approx((1 + augmented).prod() - (1 + baseline).prod())
    assert summary["signal_similarity"]["value"] == pytest.approx(-1)
    assert summary["signal_similarity"]["security_sessions_common"] == 120
    assert summary["signal_similarity"]["security_sessions_union"] == 160
    with pytest.raises(ValueError, match="PAIRED_RETURN_SESSIONS_REQUIRED"):
        _paired_metrics(baseline.iloc[1:], augmented, (left, right), 3)


@pytest.mark.asyncio
async def test_distinct_strategies_compute_concurrently_and_identical_runs_share_one_execution(tmp_path, monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from quantgpt.models import Base, User
    from quantgpt.research import strategies
    from quantgpt.research.contracts import EvaluationConfigV1, FactorDefinitionV1
    from quantgpt.research.evaluations import evaluate_factor_batch
    from quantgpt.research.preparation import prepare_project_demo
    from quantgpt.research.projects import create_project
    from quantgpt.strategy.spec import StrategySpecV2

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'parallel-strategies.sqlite'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    root = tmp_path / "snapshots"
    actor = uuid.uuid4()
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as session:
            session.add(User(id=actor, email="parallel-strategies@example.invalid", is_active=True))
            await session.flush()
            project = await create_project(session, actor, name="Parallel strategies", market="demo_global_equity")
            demo = await prepare_project_demo(session, actor, project.id, snapshot_root=root)
            config = EvaluationConfigV1.model_validate(demo["config"])
            evaluation = (await evaluate_factor_batch(session, actor, project.id,
                [FactorDefinitionV1.model_validate(demo["definitions"][0])], config, snapshot_root=root))["evaluations"][0]
        first = demo["strategy_template"]
        reference = {k: evaluation[k] for k in ("evaluation_id", "project_id", "evaluation_hash", "definition_hash", "backend")}
        first["factor_evaluations"] = [{**reference, "scope": "selection"}]
        second = deepcopy(first)
        second["name"] = "Top three"
        second["signal_rules"]["top_n"] = 3
        barrier = threading.Barrier(2, timeout=30)
        actual = strategies._compute_strategy_artifacts
        calls = []

        def observe(spec, *args):
            calls.append(spec.name)
            barrier.wait()
            return actual(spec, *args)

        monkeypatch.setattr(strategies, "_compute_strategy_artifacts", observe)

        async def submit(spec):
            async with factory() as session:
                return await strategies.run_strategy_evaluation(session, actor, project.id, StrategySpecV2.model_validate(spec),
                                                                 config, snapshot_root=root)

        a, b, repeated = await asyncio.wait_for(asyncio.gather(submit(first), submit(second), submit(first)), timeout=90)
        assert a == repeated and a["strategy_run_id"] != b["strategy_run_id"]
        assert sorted(calls) == sorted([first["name"], second["name"]])
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_compare_persisted_augmented_strategy_against_round_baseline(tmp_path):
    import shutil
    import sys
    from functools import partial
    from pathlib import Path

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    from tests.test_research_round_mcp import call as call_mcp
    from tests.test_research_round_mcp import completed

    checkout = tmp_path / "server"
    repository = Path(__file__).resolve().parents[1]
    shutil.copytree(repository / "quantgpt", checkout / "quantgpt", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(repository / "requirements", checkout / "requirements")
    server = StdioServerParameters(command=sys.executable, args=["-B", "-m", "quantgpt", "--transport", "stdio"],
        cwd=checkout, env={"DATABASE_URL": f"sqlite+aiosqlite:///{tmp_path / 'baseline.sqlite'}",
                           "QUANTGPT_RESEARCH_SNAPSHOT_ROOT": str(tmp_path / "private"), "QUANTGPT_CACHE_ONLY": "true",
                           "QUANTGPT_TASK_BACKEND": "thread", "AUTH_DISABLED": "false",
                           "JWT_SECRET_KEY": "test-secret-key-for-ci-only-do-not-use-in-production"})
    async with stdio_client(server) as streams, ClientSession(*streams) as client:
        await client.initialize()

        call = partial(call_mcp, client)
        project = await call("create_research_project", name="Frozen comparison", market="demo_global_equity")
        key = {"project_id": project["id"]}
        demo = await call("prepare_research_demo", **key)
        days = pd.bdate_range("2024-01-02", periods=180)
        start, end = days[100].date().isoformat(), days[-1].date().isoformat()
        await call("register_research_holdout", **key, start=start, end=end)
        config = demo["config"]
        config["window"]["end_session"] = days[94].date().isoformat()
        first = demo["definitions"][0]
        assessed = await call("evaluate_factors", **key, config=config, definitions=[first], submit_only=False)
        base_evaluation = assessed["evaluations"][0]

        def ref(row):
            return {**{name: row[name] for name in ("evaluation_id", "evaluation_hash", "definition_hash",
                                                     "project_id", "backend")}, "scope": "selection"}

        baseline_spec = demo["strategy_template"]
        baseline_spec["factor_evaluations"] = [ref(base_evaluation)]
        baseline = await call("run_research_strategy", **key, spec=baseline_spec, config=config, submit_only=False)
        baseline_run_id = baseline["strategy_run_id"]
        created = await call("create_research_round", **key, config=config, baseline_run_id=baseline_run_id,
            holdout_start=start, holdout_end=end,
            directions=[{"name": "price_level", "hypothesis": "Changes in relative prices may diversify the momentum basket."}])
        assert "round_id" in created, created
        cached_replay = await call("run_research_strategy", **key, spec=baseline_spec, config=config, submit_only=False)
        assert "error_code" in cached_replay and "strategy_run_id" not in cached_replay, cached_replay
        saved_signal = await call("get_research_artifact", **key, artifact_id=baseline["signal_ref"]["artifact_id"])
        assert saved_signal["kind"] == "signal"
        scope = {**key, "round_id": created["round_id"]}
        coordinator = {**scope, "coordinator_key": created["coordinator_key"]}
        branch = created["directions"][0]
        credential = {**scope, "direction_id": branch["direction_id"], "direction_key": branch["direction_key"]}
        second = {**first, "expression": "-rank(close)"}
        submit = await call("submit_research_direction", **credential, definitions=[second], idempotency_key="price-candidate")
        result = await completed(client, submit)
        extra = ref(result["evaluations"][0])
        await call("finish_research_direction", **credential)
        augmented = deepcopy(baseline_spec)
        augmented["factors"].append({"id": "low_price", "expression": second["expression"],
                                     "direction": config["direction"], "weight": .5})
        augmented["factor_evaluations"].append(extra)
        strategy = await completed(client, await call("submit_round_strategy", **coordinator, spec=augmented))
        request = {**scope, "augmented_run_id": strategy["strategy_run_id"]}
        report = await call("compare_research_strategies_to_baseline", **request)
        assert "artifact_ref" in report, report
        assert report["baseline_run_id"] == baseline_run_id
        assert report["added_factor_evaluations"] == [extra["evaluation_id"]]
        assert report["sessions"] > 20 and report["paired_mean_interval"]
        assert report["signal_similarity"]["comparable_sessions"] > 20
        assert report["mean_daily_turnover"]["augmented"] == strategy["metrics"]["turnover"]
        artifact = await call("get_research_artifact", **key, artifact_id=report["artifact_ref"]["artifact_id"])
        assert artifact["payload"]["source_artifacts"] == report["source_artifacts"]
        assert (await call("compare_research_strategies_to_baseline", **request))["artifact_ref"] == report["artifact_ref"]
