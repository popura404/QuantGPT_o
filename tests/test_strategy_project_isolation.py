"""Legacy persistence cannot bypass project scope or create research evidence."""

import copy
import uuid

import pytest
from sqlalchemy import select

from quantgpt.auth import create_access_token
from quantgpt.models import Experiment, Strategy, StrategyRun, Task, User
from quantgpt.research.projects import create_project, set_project_member
from quantgpt.strategy.spec import example_strategy_spec_v1

pytestmark = pytest.mark.asyncio


def _headers(user):
    return {"Authorization": "Bearer " + create_access_token(user.id, user.email)}


async def test_revoked_author_cannot_access_project_records_through_legacy_routes(
    client, db_session, test_user, auth_headers,
):
    author = User(id=uuid.uuid4(), email="strategy-author@example.com", password_hash="unused", is_active=True)
    db_session.add(author)
    await db_session.flush()
    project = await create_project(db_session, test_user.id, name="Shared research")
    await set_project_member(db_session, test_user.id, project.id, author.id, "researcher")
    spec = example_strategy_spec_v1()
    project_strategy = Strategy(id=uuid.uuid4(), user_id=author.id, project_id=project.id,
        name="Project secret", schema_version=spec["schema_version"], market=spec["market"],
        universe=spec["universe"], spec=spec)
    personal_strategy = Strategy(id=uuid.uuid4(), user_id=author.id, name="Personal", spec=spec,
        schema_version=spec["schema_version"], market=spec["market"], universe=spec["universe"])
    project_task = Task(id="project-task", user_id=author.id, project_id=project.id, status="completed")
    personal_task = Task(id="private-task", user_id=author.id, status="completed")
    other_task = Task(id="owners-task", user_id=test_user.id, status="completed")
    db_session.add_all([project_strategy, personal_strategy, project_task, personal_task, other_task])
    await db_session.flush()
    # Cover both paths carrying scope, including task-only and mixed associations.
    hidden = [StrategyRun(id=uuid.uuid4(), user_id=author.id, result={"secret": True}, **links) for links in (
        {"strategy_id": project_strategy.id},
        {"strategy_id": project_strategy.id, "task_id": personal_task.id},
        {"task_id": project_task.id},
        {"strategy_id": personal_strategy.id, "task_id": project_task.id},
    )]
    visible = StrategyRun(id=uuid.uuid4(), user_id=author.id, strategy_id=personal_strategy.id,
                          task_id=personal_task.id, result={"personal": True})
    db_session.add_all([*hidden, visible])
    await db_session.commit()
    author_headers = _headers(author)
    await set_project_member(db_session, test_user.id, project.id, author.id, None)
    await db_session.commit()

    specs = await client.get("/api/v1/strategy/specs", headers=author_headers)
    assert [row["id"] for row in specs.json()["strategies"]] == [str(personal_strategy.id)]
    denied = await client.get(f"/api/v1/strategy/specs/{project_strategy.id}", headers=author_headers)
    assert denied.status_code == 404
    runs = await client.get("/api/v1/strategy/runs", headers=author_headers)
    assert [row["id"] for row in runs.json()["runs"]] == [str(visible.id)]
    filtered = await client.get(f"/api/v1/strategy/runs?strategy_id={project_strategy.id}", headers=author_headers)
    assert filtered.status_code == 404
    for links in ({"strategy_id": str(project_strategy.id)}, {"task_id": project_task.id},
                  {"task_id": other_task.id}, {"task_id": "absent-task"}):
        forged = await client.post("/api/v1/strategy/runs", headers=author_headers,
            json={**links, "result": {"evidence_status": "caller_claimed_verified", "metrics": {"sharpe": 999}}})
        assert forged.status_code == 404, forged.text
    for run in hidden:
        denied = await client.get(f"/api/v1/research/projects/{project.id}/strategy-runs/{run.id}",
                                  headers=author_headers)
        assert denied.status_code == 404
    # Even a current project owner cannot treat an old caller-written row as server evidence.
    rejected = await client.get(f"/api/v1/research/projects/{project.id}/strategy-runs/{hidden[0].id}",
                                headers=auth_headers)
    assert rejected.status_code == 422 and "ORIGIN_REQUIRED" in rejected.text
    personal = await client.post("/api/v1/strategy/runs", headers=author_headers,
        json={"strategy_id": str(personal_strategy.id), "task_id": personal_task.id, "result": {"personal": True}})
    assert personal.status_code == 201


async def test_research_run_read_and_export_verify_server_origin_and_frozen_identity(
    client, db_session, test_user, auth_headers, tmp_path, monkeypatch,
):
    monkeypatch.setenv("QUANTGPT_RESEARCH_SNAPSHOT_ROOT", str(tmp_path / "snapshots"))
    project = await create_project(db_session, test_user.id, name="Origin regression")
    colleague = User(id=uuid.uuid4(), email="strategy-reader@example.com", password_hash="unused", is_active=True)
    db_session.add(colleague)
    await db_session.flush()
    await set_project_member(db_session, test_user.id, project.id, colleague.id, "viewer")
    await db_session.commit()
    base = f"/api/v1/research/projects/{project.id}"
    demo = (await client.post(base + "/offline-demo", headers=auth_headers)).json()
    evaluated = await client.post(base + "/evaluations", headers=auth_headers,
        json={"definitions": demo["definitions"], "config": demo["config"]})
    assert evaluated.status_code == 200, evaluated.text
    first = evaluated.json()["evaluations"][0]
    spec = demo["strategy_template"]
    spec["factor_evaluations"] = [{**{key: first[key] for key in (
        "evaluation_id", "evaluation_hash", "definition_hash", "project_id", "backend")}, "scope": "selection"}]
    request = {"spec": spec, "config": demo["config"]}
    response = await client.post(base + "/strategy-runs", headers=auth_headers, json=request)
    assert response.status_code == 200, response.text
    payload = response.json()
    run_id = uuid.UUID(payload["strategy_run_id"])
    url = base + "/strategy-runs/" + str(run_id)
    shared = await client.get(url, headers=_headers(colleague))
    assert shared.status_code == 200 and shared.json() == payload
    repeated = await client.post(base + "/strategy-runs", headers=auth_headers, json=request)
    assert repeated.status_code == 200 and repeated.json() == payload

    run = await db_session.get(StrategyRun, run_id)
    strategy = await db_session.get(Strategy, run_id)
    row = (await db_session.scalars(select(Experiment).where(
        Experiment.experiment_id == payload["evaluation_id"]))).one()
    assert row.strategy_id == strategy.id and row.strategy_run_id == run.id
    altered_spec = {**strategy.spec, "name": "Tampered"}
    altered_config = copy.deepcopy(row.evaluation_config)
    altered_config["config"]["direction"] = "lower_is_better"
    mutations = [
        (strategy, "schema_version", "strategy_spec/v1"), (strategy, "spec", altered_spec),
        (row, "strategy_id", None), (row, "strategy_run_id", None),
        (row, "evaluation_config", altered_config),
        *[(run, "result", {**payload, key: value}) for key, value in (
            ("schema_version", "caller/v1"), ("strategy_run_id", str(uuid.uuid4())),
            ("project_id", str(uuid.uuid4())), ("evaluation_id", first["evaluation_id"]),
            ("strategy_hash", "0" * 64), ("evaluation_hash", "0" * 64),
            ("metrics", {"sharpe": 999}), ("signal_ref", {"artifact_id": str(uuid.uuid4())}),
        )],
    ]
    for target, field, value in mutations:
        original = copy.deepcopy(getattr(target, field))
        setattr(target, field, value)
        await db_session.commit()
        read = await client.get(url, headers=auth_headers)
        export = await client.post(url + "/export", headers=auth_headers)
        assert read.status_code == 422, (field, read.text)
        assert export.status_code == 422, (field, export.text)
        setattr(target, field, original)
        await db_session.commit()
    assert (await client.get(url, headers=auth_headers)).status_code == 200


async def test_stale_strategy_worker_cannot_publish_over_new_attempt(monkeypatch):
    from quantgpt.routes import strategy as route
    from quantgpt.task_store import tasks

    task_id = "attempt-test"
    original = {"task_id": task_id, "status": "running", "attempt_id": "old", "revision": 0}
    tasks[task_id] = original
    persisted = []
    reports = []

    def replace_attempt(*args):
        tasks[task_id] = {"task_id": task_id, "status": "running", "attempt_id": "new", "revision": 20}
        return {"metrics": {"sharpe": 999}}

    monkeypatch.setattr(route, "_execute_strategy_backtest", replace_attempt)
    monkeypatch.setattr(route, "_execute_strategy_report", lambda *args: reports.append(args))
    monkeypatch.setattr(route, "persist_task_to_db", lambda *args: persisted.append(args))
    try:
        route._run_strategy_backtest_task(task_id, {}, "actor", attempt_id="old")
        assert tasks[task_id] == {"task_id": task_id, "status": "running", "attempt_id": "new", "revision": 20}
        assert reports == [] and persisted == []
        assert "completed_at" not in original
    finally:
        tasks.pop(task_id, None)
