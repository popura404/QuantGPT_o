"""Immutable economic hypotheses use actual project membership and audit storage."""

import asyncio
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from quantgpt.models import Base, ResearchAuditEvent, User
from quantgpt.research.contracts import (
    US_RESEARCH_FIELDS,
    DataInputRef,
    EngineIdentity,
    EvaluationConfigV1,
    EvaluationRef,
    EvaluationWindow,
    FactorDefinitionV1,
    ResearchScope,
    SimulationConfigV1,
    definition_hash,
)
from quantgpt.research.evaluations import register_evaluation
from quantgpt.research.hypotheses import get_hypothesis, register_hypothesis
from quantgpt.research.projects import ProjectAccessError, create_project, set_project_member


def definition(expression="rank(close)"):
    return FactorDefinitionV1(expression=expression, fields=(next(f for f in US_RESEARCH_FIELDS if f.name == "close"),))


def config():
    return EvaluationConfigV1(backend="local", direction="higher_is_better",
        scope=ResearchScope(market="us_equity", currency="USD", universe_id="synthetic", universe_mode="fixed_cohort",
                            universe_version="1", calendar_id="synthetic", calendar_version="1",
                            timezone="America/New_York", benchmark="none"),
        window=EvaluationWindow(start_session="2024-01-02", end_session="2024-02-01", split_id="fixture", split_hash="a" * 64),
        data_inputs=(DataInputRef(source="synthetic", feed="fixture", adjustment="raw", asof="2024-01-01T00:00:00Z"),),
        simulation_config=SimulationConfigV1(rebalance_anchor_session="2024-01-02"),
        engine=EngineIdentity(engine="python", version="test", code_version="fixture"))


@pytest.mark.asyncio
async def test_registration_is_idempotent_immutable_and_card_can_resolve_it(db_session, test_user):
    from quantgpt.research.cards import build_factor_research_card

    project = await create_project(db_session, test_user.id, name="Hypothesis")
    first = await register_hypothesis(db_session, test_user.id, project.id, definition(), economic_hypothesis="A testable mechanism")
    second = await register_hypothesis(db_session, test_user.id, project.id, definition(), economic_hypothesis="A testable mechanism")
    assert first == second
    with pytest.raises(ValueError, match="ALREADY_FROZEN"):
        await register_hypothesis(db_session, test_user.id, project.id, definition(), economic_hypothesis="Rewritten after looking")
    resolved = await get_hypothesis(db_session, test_user.id, project.id, definition_hash(definition()))
    assert resolved == first
    card = build_factor_research_card(evaluation_id="fixture", definition=definition(), config=config(), output={},
                                      hypothesis_registration=resolved)
    assert card["economic_hypothesis"] == "A testable mechanism"
    assert "ECONOMIC_HYPOTHESIS_NOT_REGISTERED" not in card["blockers"]
    assert card["research_decision"] == "not_evaluated"


@pytest.mark.asyncio
async def test_new_hypothesis_cannot_be_added_after_trial_registration(db_session, test_user):
    project = await create_project(db_session, test_user.id, name="Too late")
    await register_evaluation(db_session, test_user.id, project.id, definition(), config())
    with pytest.raises(ValueError, match="TOO_LATE"):
        await register_hypothesis(db_session, test_user.id, project.id, definition(), economic_hypothesis="Post-hoc story")


@pytest.mark.asyncio
async def test_preexisting_registration_retry_remains_valid_after_observation(db_session, test_user):
    project = await create_project(db_session, test_user.id, name="Retry")
    first = await register_hypothesis(db_session, test_user.id, project.id, definition(), economic_hypothesis="Before seeing returns")
    row, _ = await register_evaluation(db_session, test_user.id, project.id, definition(), config())
    row.result_summary = {"performance_observed": True}
    await db_session.flush()
    assert await register_hypothesis(db_session, test_user.id, project.id, definition(), economic_hypothesis="Before seeing returns") == first


@pytest.mark.asyncio
async def test_baseline_reference_requires_same_project_full_identity_and_observed_run(db_session, test_user):
    project = await create_project(db_session, test_user.id, name="Baseline")
    row, _ = await register_evaluation(db_session, test_user.id, project.id, definition("close"), config())
    ref = EvaluationRef(evaluation_id=row.experiment_id, project_id=str(project.id), evaluation_hash=row.evaluation_hash,
                        definition_hash=row.definition_hash, backend="local", scope="selection")
    with pytest.raises(ValueError, match="BASELINE_EVALUATION_REQUIRED"):
        await register_hypothesis(db_session, test_user.id, project.id, definition(), economic_hypothesis="Incremental claim", frozen_baseline=ref)
    row.result_summary = {"performance_observed": True}
    await db_session.flush()
    other = await create_project(db_session, test_user.id, name="Other")
    with pytest.raises(ProjectAccessError):
        await register_hypothesis(db_session, test_user.id, other.id, definition(), economic_hypothesis="Other", frozen_baseline=ref)
    forged = ref.model_copy(update={"evaluation_hash": "b" * 64})
    with pytest.raises(ValueError, match="REFERENCE_INTEGRITY"):
        await register_hypothesis(db_session, test_user.id, project.id, definition(), economic_hypothesis="Forged", frozen_baseline=forged)
    registered = await register_hypothesis(db_session, test_user.id, project.id, definition(), economic_hypothesis="Incremental claim", frozen_baseline=ref)
    assert registered["frozen_baseline"]["evaluation_hash"] == row.evaluation_hash


@pytest.mark.asyncio
async def test_revoked_membership_and_audit_tamper_are_rejected(db_session, test_user):
    project = await create_project(db_session, test_user.id, name="Scope")
    colleague = User(id=uuid.uuid4(), email="hypothesis@example.invalid", is_active=True)
    db_session.add(colleague)
    await db_session.flush()
    await set_project_member(db_session, test_user.id, project.id, colleague.id, "researcher")
    registered = await register_hypothesis(db_session, colleague.id, project.id, definition(), economic_hypothesis="A mechanism")
    await set_project_member(db_session, test_user.id, project.id, colleague.id, None)
    with pytest.raises(ProjectAccessError):
        await get_hypothesis(db_session, colleague.id, project.id, definition_hash(definition()))
    event = await db_session.get(ResearchAuditEvent, uuid.UUID(registered["audit_event_id"]))
    event.payload = {**event.payload, "economic_hypothesis": "tampered"}
    await db_session.flush()
    with pytest.raises(ValueError, match="INTEGRITY"):
        await get_hypothesis(db_session, test_user.id, project.id, definition_hash(definition()))


@pytest.mark.asyncio
async def test_concurrent_registration_cannot_overwrite_first_hypothesis(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///" + (tmp_path / "hypotheses.sqlite").as_posix())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as session:
            actor = uuid.uuid4()
            session.add(User(id=actor, email="parallel@example.invalid", is_active=True))
            await session.flush()
            project = await create_project(session, actor, name="Concurrency")
            project_id = project.id
            await session.commit()

        async def attempt(text):
            async with factory() as session:
                try:
                    result = await register_hypothesis(session, actor, project_id, definition(), economic_hypothesis=text)
                    await session.commit()
                    return result
                except ValueError:
                    await session.rollback()
                    return None

        results = await asyncio.gather(attempt("First idea"), attempt("Second idea"))
        assert sum(result is not None for result in results) == 1
        async with factory() as session:
            count = await session.scalar(select(func.count()).select_from(ResearchAuditEvent).where(
                ResearchAuditEvent.project_id == project_id, ResearchAuditEvent.action == "hypothesis_registered"))
            assert count == 1
    finally:
        await engine.dispose()
