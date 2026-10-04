"""Evaluation artifacts and holdout exposure use the real database services."""

import uuid

import pytest

from quantgpt.models import User
from quantgpt.research.contracts import (
    US_RESEARCH_FIELDS,
    DataInputRef,
    EngineIdentity,
    EvaluationConfigV1,
    EvaluationWindow,
    FactorDefinitionV1,
    ResearchScope,
    SimulationConfigV1,
)
from quantgpt.research.evaluations import (
    guard_evaluation_window,
    read_artifact,
    register_evaluation,
    register_holdout,
    write_artifact,
)
from quantgpt.research.projects import ProjectAccessError, create_project


def config(phase="selection", start="2024-01-02", end="2024-02-01"):
    return EvaluationConfigV1(backend="local", direction="higher_is_better",
        scope=ResearchScope(market="us_equity", currency="USD", universe_id="fixture", universe_mode="fixed_cohort",
                            universe_version="1", calendar_id="synthetic", calendar_version="1",
                            timezone="America/New_York", benchmark="fixture"),
        window=EvaluationWindow(start_session=start, end_session=end, phase=phase, split_id="registered",
                                split_hash="a" * 64),
        data_inputs=(DataInputRef(source="synthetic", feed="fixture", adjustment="raw", asof="2024-01-01T00:00:00Z"),),
        simulation_config=SimulationConfigV1(rebalance_anchor_session="2024-01-02"),
        engine=EngineIdentity(engine="python", version="fixture", code_version="fixture"))


def definition(expression="rank(close)"):
    return FactorDefinitionV1(expression=expression, fields=(next(f for f in US_RESEARCH_FIELDS if f.name == "close"),))


@pytest.mark.asyncio
async def test_evaluation_retry_and_artifact_scope(db_session, test_user):
    project = await create_project(db_session, test_user.id, name="Research")
    first, created = await register_evaluation(db_session, test_user.id, project.id, definition(), config())
    second, created_again = await register_evaluation(db_session, test_user.id, project.id, definition(), config())
    assert created and not created_again
    assert first.experiment_id == second.experiment_id
    artifact = await write_artifact(db_session, first, "returns", [{"return": .01}])
    assert (await read_artifact(db_session, test_user.id, project.id, uuid.UUID(artifact["artifact_id"])))["payload"] == [{"return": .01}]
    other = await create_project(db_session, test_user.id, name="Different project")
    with pytest.raises(ProjectAccessError):
        await read_artifact(db_session, test_user.id, other.id, uuid.UUID(artifact["artifact_id"]))
    outsider = User(id=uuid.uuid4(), email="outsider@test", is_active=True)
    db_session.add(outsider)
    await db_session.flush()
    with pytest.raises(ProjectAccessError):
        await read_artifact(db_session, outsider.id, project.id, uuid.UUID(artifact["artifact_id"]))


@pytest.mark.asyncio
async def test_final_lock_cannot_be_reset_by_changing_candidate_or_hash(db_session, test_user):
    project = await create_project(db_session, test_user.id, name="Holdout")
    await register_holdout(db_session, test_user.id, project.id, start="2024-03-01", end="2024-03-29")
    final = config("final", "2024-03-01", "2024-03-29")
    first, _ = await register_evaluation(db_session, test_user.id, project.id, definition(), final)
    await guard_evaluation_window(db_session, test_user.id, first, final)
    await db_session.commit()
    await guard_evaluation_window(db_session, test_user.id, first, final)
    different, _ = await register_evaluation(db_session, test_user.id, project.id, definition("-rank(close)"), final)
    with pytest.raises(ValueError, match="ALREADY_EXPOSED"):
        await guard_evaluation_window(db_session, test_user.id, different, final)


@pytest.mark.asyncio
async def test_selection_cannot_observe_test_window(db_session, test_user):
    project = await create_project(db_session, test_user.id, name="Selection")
    await register_holdout(db_session, test_user.id, project.id, start="2024-03-01", end="2024-03-29")
    selection = config(end="2024-03-02")
    row, _ = await register_evaluation(db_session, test_user.id, project.id, definition(), selection)
    with pytest.raises(ValueError, match="WITHHELD"):
        await guard_evaluation_window(db_session, test_user.id, row, selection)


@pytest.mark.asyncio
async def test_selection_after_holdout_cannot_read_it_as_warmup(db_session, test_user):
    project = await create_project(db_session, test_user.id, name="Later window leak")
    await register_holdout(db_session, test_user.id, project.id, start="2024-03-01", end="2024-03-29")
    selection = config(start="2024-04-01", end="2024-05-01")
    row, _ = await register_evaluation(db_session, test_user.id, project.id, definition(), selection)
    with pytest.raises(ValueError, match="WITHHELD"):
        await guard_evaluation_window(db_session, test_user.id, row, selection)
