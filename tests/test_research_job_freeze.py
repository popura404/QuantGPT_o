"""Durable acceptance freezes effective server settings across deployment changes."""

import pytest

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
from quantgpt.research.jobs import execute_research_request, submit_research_request
from quantgpt.research.projects import create_project
from quantgpt.research.runtime import local_engine_identity
from quantgpt.task_store import claim_durable_task, load_durable_task


def _request():
    config = EvaluationConfigV1(backend="local", direction="higher_is_better",
        scope=ResearchScope(market="us_equity", currency="USD", universe_id="fixture", universe_mode="fixed_cohort",
                            universe_version="1", calendar_id="synthetic", calendar_version="1",
                            timezone="America/New_York", benchmark="fixture"),
        window=EvaluationWindow(start_session="2024-01-02", end_session="2024-02-01", split_id="fixture", split_hash="a" * 64),
        data_inputs=(DataInputRef(source="synthetic", feed="fixture", adjustment="raw", asof="2024-01-01T00:00:00Z"),),
        simulation_config=SimulationConfigV1(rebalance_anchor_session="2024-01-02"),
        engine=EngineIdentity(engine="python", version="caller-claimed", code_version="caller-claimed"))
    definition = FactorDefinitionV1(expression="close", fields=(US_RESEARCH_FIELDS[2],))
    return {"kind": "factor", "config": config.model_dump(mode="json"), "definitions": [definition.model_dump(mode="json")]}


@pytest.mark.asyncio
async def test_effective_config_frozen_before_dispatch_and_changed_worker_rejected(db_session, test_user, monkeypatch):
    project = await create_project(db_session, test_user.id, name="Frozen queue")
    request = _request()
    expected = local_engine_identity()
    accepted = await submit_research_request(db_session, test_user.id, project.id, request)
    task = await load_durable_task(db_session, accepted["task_id"], str(test_user.id))
    assert task["params"]["config"]["engine"] == expected.model_dump(mode="json")
    assert request["config"]["engine"]["version"] == "caller-claimed"
    context = await claim_durable_task(db_session, accepted["task_id"])
    monkeypatch.setattr("quantgpt.research.jobs.local_engine_identity", lambda: expected.model_copy(update={"code_version": "changed"}))
    with pytest.raises(ValueError, match="CODE_VERSION_CHANGED"):
        await execute_research_request(db_session, test_user.id, project.id, context["params"], task_context=context)


@pytest.mark.asyncio
async def test_unknown_request_fields_rejected_before_durable_acceptance(db_session, test_user):
    project = await create_project(db_session, test_user.id, name="No unknown settings")
    request = {**_request(), "extra_behavior": "silently ignored"}
    with pytest.raises(ValueError, match="UNKNOWN_RESEARCH_REQUEST_FIELDS"):
        await submit_research_request(db_session, test_user.id, project.id, request)
