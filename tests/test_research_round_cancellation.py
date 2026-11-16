"""Task control uses branch credentials even when agents share one principal."""

import asyncio
import hashlib
import uuid

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from quantgpt.models import ResearchAuditEvent, ResearchDirection, ResearchRound, Task, User
from quantgpt.research.contracts import content_hash
from quantgpt.research.evaluations import assert_publication_attempt
from quantgpt.research.projects import ProjectAccessError, create_project
from quantgpt.research.round_final import finalize_round
from quantgpt.research.rounds import cancel_round_task, finish_direction, submit_direction
from quantgpt.task_store import CancelledException, RoundTaskCredentialRequired, claim_durable_task, load_durable_task


@pytest.mark.asyncio
async def test_cancelled_final_resumes_same_frozen_request(db_session, client, test_user, auth_headers, engine, monkeypatch):
    from quantgpt import db
    from quantgpt.research import jobs
    from quantgpt.task_store import retry_durable_task

    actor = test_user.id
    project = await create_project(db_session, actor, name="Frozen task resumption")
    selection = uuid.uuid4()
    round_id = uuid.uuid4()
    coord_key, dir_key = uuid.uuid4().hex, uuid.uuid4().hex
    frozen = {"round_id": str(round_id), "selection_run_id": str(selection), "final_evaluation_id": "se_frozen",
              "spec": {"name": "frozen strategy"}, "config": {"backend": "local"}}
    frozen["freeze_hash"] = content_hash("quantgpt.round_freeze/v1", frozen)
    row = ResearchRound(id=round_id, project_id=project.id, coordinator_id=actor,
        coordinator_digest=hashlib.sha256(coord_key.encode()).hexdigest(), selection_config={}, holdout={},
        status="frozen", frozen=frozen, final_evaluation_id="se_frozen")
    db_session.add(row)
    await db_session.flush()
    direction = ResearchDirection(id=uuid.uuid4(), round_id=round_id, name="Trend", hypothesis="Predeclared trend",
        actor_id=actor, credential_digest=hashlib.sha256(dir_key.encode()).hexdigest())
    db_session.add(direction)
    await db_session.commit()
    request = {"round_id": round_id, "coordinator_key": coord_key, "selection_run_id": selection}
    accepted = await finalize_round(db_session, actor, project.id, **request)
    task_id = accepted["task_id"]
    first = await claim_durable_task(db_session, task_id)
    assert first
    generic = await client.post(f"/api/v1/tasks/{task_id}/cancel", headers=auth_headers)
    assert generic.status_code == 409 and "cancel_research_round_task" in generic.text
    with pytest.raises(ProjectAccessError):
        await cancel_round_task(db_session, actor, project.id, round_id=round_id, task_id=task_id,
                                direction_id=direction.id, direction_key=dir_key)
    cancelled = await cancel_round_task(db_session, actor, project.id, round_id=round_id, task_id=task_id,
                                        coordinator_key=coord_key)
    assert cancelled["status"] == "cancelled" and cancelled["selection_run_id"] == str(selection)
    with pytest.raises(ValueError, match="ALREADY_FROZEN"):
        await finalize_round(db_session, actor, project.id, **{**request, "selection_run_id": uuid.uuid4()})
    resumed = await finalize_round(db_session, actor, project.id, **request)
    assert resumed["task_id"] == task_id and resumed["status"] == "queued" and not resumed["created"]
    second = await claim_durable_task(db_session, task_id)
    assert second["params"] == first["params"] and second["attempt_id"] != first["attempt_id"]
    with pytest.raises(CancelledException):
        await assert_publication_attempt(db_session, first)
    await assert_publication_attempt(db_session, second)
    assert (await load_durable_task(db_session, task_id, str(actor)))["cancel_requested"] is False
    await db_session.commit()
    attempts = []

    async def temporary_failure(*args):
        attempts.append(True)
        raise TimeoutError("snapshot storage temporarily unavailable")

    monkeypatch.setattr(jobs, "_execute_once", temporary_failure)
    monkeypatch.setattr(db, "_get_session_factory", lambda: async_sessionmaker(engine, expire_on_commit=False))
    await asyncio.to_thread(jobs.run_research_job, second, event_loop=asyncio.get_running_loop())
    saved = await db_session.get(type(row), round_id, populate_existing=True)
    assert saved.frozen == frozen
    interrupted = await load_durable_task(db_session, task_id, str(actor))
    assert len(attempts) == 3
    assert interrupted["status"] == "interrupted" and interrupted["retryable"]
    with pytest.raises(RoundTaskCredentialRequired):
        await retry_durable_task(db_session, task_id, str(actor))
    after_io = await finalize_round(db_session, actor, project.id, **request)
    assert after_io["task_id"] == task_id and after_io["status"] == "queued"
    assert (await load_durable_task(db_session, task_id, str(actor)))["params"] == first["params"]
    assert row.frozen == frozen


@pytest.mark.parametrize(("error", "expected"), [(PermissionError("denied"), False),
    (ValueError("incompatible frozen inputs"), False), (TimeoutError("timeout"), True), (OSError("disk"), True)])
def test_only_recoverable_final_errors_retry(error, expected):
    from quantgpt.research.jobs import _retryable_execution_error

    assert _retryable_execution_error({"kind": "round_final"}, error) is expected
    assert not _retryable_execution_error({"kind": "factor"}, error)


@pytest.mark.asyncio
async def test_finished_direction_can_recover_or_abandon_only_original_work(db_session, test_user):
    from quantgpt.research.runtime import normalize_local_config
    from tests.test_research_hypotheses import config, definition

    actor = test_user.id
    project = await create_project(db_session, actor, name="Finish before task exits")
    round_id, direction_id = uuid.uuid4(), uuid.uuid4()
    coordinator_key, direction_key = uuid.uuid4().hex, uuid.uuid4().hex
    row = ResearchRound(id=round_id, project_id=project.id, coordinator_id=actor,
        coordinator_digest=hashlib.sha256(coordinator_key.encode()).hexdigest(),
        selection_config=normalize_local_config(config()).model_dump(mode="json"), holdout={}, status="open")
    db_session.add(row)
    await db_session.flush()
    db_session.add(ResearchDirection(id=direction_id, round_id=round_id, name="Direction", hypothesis="Independent idea",
        actor_id=actor, credential_digest=hashlib.sha256(direction_key.encode()).hexdigest()))
    await db_session.commit()
    access = {"round_id": round_id, "direction_id": direction_id, "direction_key": direction_key}
    payload = {**access, "definitions": [definition().model_dump(mode="json")]}
    first = await submit_direction(db_session, actor, project.id, **payload, idempotency_key="original")
    second = await submit_direction(db_session, actor, project.id, **payload, idempotency_key="abandon")
    prior = await claim_durable_task(db_session, first["task_id"])
    await finish_direction(db_session, actor, project.id, **access)
    await db_session.execute(update(Task).where(Task.id.in_([first["task_id"], second["task_id"]])).values(
        status="interrupted", retryable=True, lease_expires_at=None, dispatch_pending=False))
    await db_session.commit()
    with pytest.raises(ValueError, match="EXISTING_TASK_REQUIRED"):
        await submit_direction(db_session, actor, project.id, **payload, idempotency_key="new-candidate")
    with pytest.raises(ValueError, match="IDEMPOTENCY_CONFLICT"):
        await submit_direction(db_session, actor, project.id, **{**payload,
            "definitions": [definition("-rank(close)").model_dump(mode="json")]}, idempotency_key="original")
    resumed = await submit_direction(db_session, actor, project.id, **payload, idempotency_key="original")
    assert not resumed["created"] and resumed["task_id"] == first["task_id"] and resumed["status"] == "queued"
    with pytest.raises(CancelledException):
        await assert_publication_attempt(db_session, prior)
    abandoned = await cancel_round_task(db_session, actor, project.id, **access, task_id=second["task_id"])
    assert abandoned["status"] == "cancelled"
    assert (await submit_direction(db_session, actor, project.id, **payload, idempotency_key="abandon"))["status"] == "cancelled"
    assert await db_session.scalar(select(func.count()).select_from(Task)) == 2


@pytest.mark.asyncio
async def test_coordinator_can_close_an_unavailable_researchers_direction(db_session, test_user):
    project = await create_project(db_session, test_user.id, name="Coordinator takeover")
    departed = uuid.uuid4()
    db_session.add(User(id=departed, email="departed@example.invalid", is_active=False))
    await db_session.flush()
    secret = "coordinator-secret"
    row = ResearchRound(id=uuid.uuid4(), project_id=project.id, coordinator_id=test_user.id,
                        coordinator_digest=hashlib.sha256(secret.encode()).hexdigest(), selection_config={}, holdout={})
    db_session.add(row)
    await db_session.flush()
    branch = ResearchDirection(id=uuid.uuid4(), round_id=row.id, actor_id=departed, name="liquidity",
                               hypothesis="A separate liquidity premium", credential_digest="0" * 64)
    db_session.add(branch)
    await db_session.flush()
    kwargs = {"round_id": row.id, "direction_id": branch.id}
    with pytest.raises(ProjectAccessError):
        await finish_direction(db_session, test_user.id, project.id, **kwargs, direction_key=secret)
    for _ in range(2):
        assert (await finish_direction(db_session, test_user.id, project.id, **kwargs, coordinator_key=secret))["finished"]
    events = (await db_session.scalars(select(ResearchAuditEvent).where(
        ResearchAuditEvent.action == "round_direction_finished"))).all()
    assert len(events) == 1 and events[0].payload["authority"] == "coordinator"
