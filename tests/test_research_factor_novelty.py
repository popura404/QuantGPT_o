import asyncio
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from quantgpt.models import Base, Experiment, FactorPoolEntry, SavedFactor, SubmittedAlpha, Task, User
from quantgpt.research.novelty import check_factor_novelty
from quantgpt.research.preparation import prepare_project_demo
from quantgpt.research.projects import ProjectAccessError, create_project
from quantgpt.research.rounds import create_round, submit_direction


@pytest.mark.asyncio
async def test_project_and_personal_libraries_hint_at_parameter_variants_without_exposing_other_users(db_session, test_user):
    actor = test_user.id
    project = await create_project(db_session, actor, name="Discover")
    other = uuid.uuid4()
    db_session.add(User(id=other, email="separate@example.invalid", is_active=True))
    await db_session.flush()
    private = await create_project(db_session, other, name="Private")
    db_session.add(SavedFactor(user_id=actor, expression="rank(delta(close, 4))"))
    db_session.add(SavedFactor(user_id=other, expression="rank(open)"))
    db_session.add(SubmittedAlpha(user_id=actor, alpha_id="submitted-one", expression="rank(high)"))
    db_session.add(SubmittedAlpha(user_id=other, alpha_id="private", expression="rank(open)"))
    db_session.add(Experiment(user_id=actor, project_id=project.id, experiment_id="old-candidate",
                              factor_hash="old-hash", expression="ts_rank(high,15)", expression_normalized="ts_rank(high,15)",
                              evaluation_config=None, status="candidate"))
    db_session.add(Experiment(user_id=actor, project_id=project.id, experiment_id="wq-observed", backend="wq",
                              factor_hash="wq-hash", definition_hash="wq-def", expression="rank(vwap)",
                              expression_normalized="rank(vwap)", status="completed", result_summary={"performance_observed": True},
                              evaluation_config={"config": {"window": {"phase": "platform"}}}))
    db_session.add(FactorPoolEntry(owner_user_id=actor, project_id=project.id, expression="ts_mean(volume, 20)",
                                  expression_normalized="ts_mean(volume,20)"))
    db_session.add(FactorPoolEntry(owner_user_id=other, project_id=private.id, expression="rank(low)",
                                  expression_normalized="rank(low)"))
    await db_session.commit()
    expressions = ["rank(ts_delta(close,4))", "rank(delta(close, 7))", "ts_mean(volume, 20)", "rank(open)", "rank(low)",
                   "RANK((ts_delta(close, 4)))", "ts_std_dev(close, 10)", "stddev(close, 10)", "rank(high)", "ts_rank(high,15)",
                   "rank(vwap)"]
    report = await check_factor_novelty(db_session, actor, project.id, expressions=expressions)
    assert report["new_indices"] == [1, 3, 4, 6]
    hits = report["candidates"]
    assert hits[0]["exact_matches"][0]["source"] == "personal_favorite"
    assert hits[1]["family_count"] == 1 and hits[2]["exact_matches"][0]["source"] == "factor_pool"
    assert hits[5]["duplicate_of_index"] == 0 and hits[7]["duplicate_of_index"] == 6
    assert hits[8]["exact_matches"][0]["source"] == "submitted_alpha"
    assert hits[9]["exact_matches"][0]["id"] == "old-candidate"
    assert hits[10]["exact_matches"][0]["backend"] == "wq"
    with pytest.raises(ProjectAccessError):
        await check_factor_novelty(db_session, other, project.id, expressions=expressions)
    assert await db_session.scalar(select(func.count()).select_from(Experiment)) == 2


@pytest.mark.asyncio
async def test_two_directions_claim_one_new_expression_and_one_can_continue_with_a_variant(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'novel.sqlite'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    actor = uuid.uuid4()
    snapshots = tmp_path / "snapshots"
    monkeypatch.setenv("QUANTGPT_RESEARCH_SNAPSHOT_ROOT", str(snapshots))
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as session:
            session.add(User(id=actor, email="research@example.invalid", is_active=True))
            await session.flush()
            project = await create_project(session, actor, name="Two branches", market="demo_global_equity")
            pid = project.id
            demo = await prepare_project_demo(session, actor, pid, snapshot_root=snapshots)
            created = await create_round(session, actor, pid, **demo["round_setup"],
                directions=[{"name": n, "hypothesis": n} for n in ["trend", "reversal"]])
            await session.commit()
        round_id = uuid.UUID(created["round_id"])
        expr = "rank(ts_delta(close, 5))"
        variant = "rank(ts_delta(close, 10))"

        async def submit(branch, expressions, key):
            async with factory() as session:
                result = await submit_direction(session, actor, pid, round_id=round_id, direction_id=uuid.UUID(branch["direction_id"]),
                    direction_key=branch["direction_key"], definitions=[{**demo["definitions"][0], "expression": e} for e in expressions],
                    idempotency_key=key, only_new=True)
                await session.commit()
                return result

        branches = created["directions"]
        answers = await asyncio.gather(*(submit(b, [expr], "discover") for b in branches))
        winner = next(i for i, answer in enumerate(answers) if answer["created"])
        pending = answers[winner]["task_id"]
        duplicate = answers[1 - winner]
        assert duplicate["status"] == "known_candidates" and "task_id" not in duplicate
        assert duplicate["candidates"][0]["exact_matches"][0]["id"] == pending
        assert (await submit(branches[winner], [expr], "discover"))["task_id"] == pending
        mixed = await submit(branches[1 - winner], ["rank(delta(close,5))", variant], "alternatives")
        assert mixed["new_indices"] == [1] and mixed["candidates"][1]["family_count"] == 1
        assert (await submit(branches[1 - winner], [variant], "variant"))["created"]
        async with factory() as session:
            assert await session.scalar(select(func.count()).select_from(Task)) == 2
            assert await session.scalar(select(func.count()).select_from(Experiment)) == 0
    finally:
        await engine.dispose()
