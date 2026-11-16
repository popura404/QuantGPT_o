"""Exercise pre-round publication and historical provenance at the durable task boundary."""

import asyncio
import threading
import uuid

import pandas as pd
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from quantgpt import backtest, db
from quantgpt.models import (
    Base,
    FactorSearchAttempt,
    FinalWindowExposure,
    ResearchAuditEvent,
    ResearchRound,
    Task,
    User,
)
from quantgpt.research.demo import prepare_offline_demo
from quantgpt.research.evaluations import register_holdout
from quantgpt.research.jobs import run_research_job, submit_research_request
from quantgpt.research.projects import create_project
from quantgpt.research.round_final import finalize_round
from quantgpt.research.rounds import create_round, finish_direction, import_iteration, submit_direction, submit_strategy
from quantgpt.task_store import claim_durable_task


@pytest.mark.asyncio
@pytest.mark.parametrize("overlaps", [False, True])
async def test_preround_work_and_legacy_dates_before_final_exposure(tmp_path, monkeypatch, overlaps):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'history.sqlite'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db, "_get_session_factory", lambda: factory)
    root = tmp_path / "snapshots"
    monkeypatch.setenv("QUANTGPT_RESEARCH_SNAPSHOT_ROOT", str(root))
    demo = prepare_offline_demo(root)
    days = pd.bdate_range("2024-01-02", periods=180)
    begin, end = str(days[100].date()), str(days[-1].date())
    config = demo["config"]
    config["window"]["end_session"] = str(days[94].date())
    actor = uuid.uuid4()
    ready, release = threading.Event(), threading.Event()
    calculate = backtest.run_factor_backtest

    def paused_calculation(*args, **kwargs):
        if kwargs["expression"] == "-rank(close)":
            ready.set()
            assert release.wait(60)
        return calculate(*args, **kwargs)

    monkeypatch.setattr(backtest, "run_factor_backtest", paused_calculation)

    async def run(accepted):
        async with factory() as session:
            payload = await claim_durable_task(session, accepted["task_id"])
        await asyncio.to_thread(run_research_job, payload, event_loop=asyncio.get_running_loop())
        async with factory() as session:
            task = await session.get(Task, accepted["task_id"])
            assert task.status == "completed", task.error
            return task.result

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        session.add(User(id=actor, email="history@example.invalid", is_active=True))
        await session.flush()
        pid = (await create_project(session, actor, name="Historical selection")).id
        session.add(ResearchAuditEvent(project_id=pid, actor_id=actor, action="snapshot_registered",
            payload={"snapshot_id": config["data_inputs"][0]["manifest_id"]}))
        await register_holdout(session, actor, pid, start=begin, end=end)
        await session.commit()
        accepted = await submit_research_request(session, actor, pid, {"kind": "factor", "config": config,
            "definitions": [{**demo["definitions"][0], "expression": "-rank(close)"}]})
    pending = asyncio.create_task(run(accepted))
    open_request = {"config": config, "holdout_start": begin, "holdout_end": end,
                    "directions": [{"name": "Trend", "hypothesis": "Trend persistence."}]}
    try:
        assert await asyncio.to_thread(ready.wait, 15)
        async with factory() as session:
            with pytest.raises(ValueError, match="PREROUND_SELECTION_PENDING"):
                await create_round(session, actor, pid, **open_request)
        release.set()
        assert (await pending)["evaluations"][0]["summary"]["performance_observed"]
        async with factory() as session:
            created = await create_round(session, actor, pid, **open_request)
            await session.commit()
        rid = uuid.UUID(created["round_id"])
        branch = created["directions"][0]
        role = {"round_id": rid, "direction_id": uuid.UUID(branch["direction_id"]), "direction_key": branch["direction_key"]}
        coord = {"round_id": rid, "coordinator_key": created["coordinator_key"]}
        attempt_id = uuid.uuid4()
        async with factory() as session:
            session.add(Task(id="historic", user_id=actor, task_type="iteration", status="iteration_completed",
                result={"search_attempts": [{"id": str(attempt_id), "expression": "rank(close)", "status": "success", "raw_score": 0}]}))
            await session.flush()
            imported = await import_iteration(session, actor, pid, **role, task_id="historic")
            assert imported["holdout_review"]["status"] == "unverified"
            proposed = await submit_direction(session, actor, pid, **role, definitions=demo["definitions"])
        factor = (await run(proposed))["evaluations"][0]
        spec = demo["strategy_template"]
        spec["factor_evaluations"] = [{**{key: factor[key] for key in (
            "evaluation_id", "evaluation_hash", "definition_hash", "project_id", "backend")}, "scope": "selection"}]
        async with factory() as session:
            selected = await submit_strategy(session, actor, pid, **coord, spec=spec)
        selection_id = uuid.UUID((await run(selected))["strategy_run_id"])
        async with factory() as session:
            await finish_direction(session, actor, pid, **role)
            await session.commit()
            with pytest.raises(ValueError, match="LEGACY_HOLDOUT_EVIDENCE_INCOMPLETE"):
                await finalize_round(session, actor, pid, **coord, selection_run_id=selection_id)
            await session.rollback()
            # Restore a missing historical search ledger record; the original
            # task, import and locked project window retain their identities.
            session.add(FactorSearchAttempt(id=attempt_id, user_id=actor, task_id="historic", status="success", failed=False,
                raw_score=0., expression="rank(close)", expression_key="rank(close)", family_key="rank", scope_key="archive",
                params={"market": config["scope"]["market"], "universe": config["scope"]["universe_id"],
                        "start_date": begin if overlaps else "2023-01-02", "end_date": end if overlaps else "2023-12-29"}))
            await session.commit()
            qualified = await import_iteration(session, actor, pid, **role, task_id="historic")
            assert qualified["holdout_review"]["status"] == ("exposed" if overlaps else "clear")
            await session.commit()
            if overlaps:
                with pytest.raises(ValueError, match="LEGACY_HOLDOUT_EXPOSED"):
                    await finalize_round(session, actor, pid, **coord, selection_run_id=selection_id)
                assert await session.scalar(select(func.count()).select_from(FinalWindowExposure)) == 0
                assert (await session.get(ResearchRound, rid)).status == "open"
            else:
                final = await finalize_round(session, actor, pid, **coord, selection_run_id=selection_id)
        if not overlaps:
            report = (await run(final))["validation"]
            assert report["observed_project_trials"] == 4
            assert report["checks"]["strategy_oos"]["status"] == "passed"
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)
        await engine.dispose()
