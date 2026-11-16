"""Imported search scores preserve their statistical family and holdout provenance."""

import uuid

import pytest
from sqlalchemy import select

from quantgpt.models import FactorSearchAttempt, ResearchDirection, ResearchRound, Task
from quantgpt.research.contracts import content_hash
from quantgpt.research.legacy_history import review_legacy_windows
from quantgpt.research.projects import create_project
from quantgpt.research.round_final import final_validation_context
from quantgpt.research.validation import build_trial_manifest
from quantgpt.search_ledger import IncompleteIterationHistory, load_observed_iteration_trials


@pytest.mark.asyncio
async def test_shared_legacy_parent_and_observed_cancelled_child_count_once(db_session, test_user):
    actor = test_user.id
    project = await create_project(db_session, actor, name="Family")
    dates = {"start_date": "2023-02-01", "end_date": "2023-04-03", "universe": "demo"}
    parent = Task(id="source", user_id=actor, task_type="backtest", status="completed", expression="rank(volume)",
                  result={"params": dates, "metrics": {"ic_mean": .02}})
    db_session.add(parent)
    await db_session.flush()
    for name, expr in [("iterA", "-rank(close)"), ("iterB", "rank(close)")]:
        db_session.add(Task(id=name, user_id=actor, task_type="iteration", status="iteration_completed",
            parent_task_id=parent.id, params=dates, result={"parent_task_id": parent.id, "parent_expression": parent.expression,
            "parent_score": 0, "candidates": [{"expression": expr, "score": 0, "status": "success"}]}))
    db_session.add(Task(id="iterC", user_id=actor, parent_task_id=parent.id, task_type="iteration", status="cancelled"))
    db_session.add(Task(id="no-score", user_id=actor, parent_task_id=parent.id, task_type="iteration", status="cancelled"))
    await db_session.flush()
    db_session.add(FactorSearchAttempt(id=uuid.uuid4(), user_id=actor, task_id="iterC", status="success", failed=False,
        raw_score=31.5, expression="rank(open)", expression_key="rank(open)", family_key="rank", scope_key="old", params=dates))
    await db_session.flush()
    imported = []
    for task_id in ["iterA", "iterB", "iterC", "no-score", "iterA"]:
        imported.extend(await load_observed_iteration_trials(db_session, actor, task_id, project_id=project.id))
    manifest = await build_trial_manifest(db_session, project.id, [], legacy_trials=imported)
    assert manifest["observed_trials"] == manifest["legacy_trials"] == 4
    assert manifest["trial_keys"].count("legacy-parent:source") == 1
    assert not any(r["task_id"] == "no-score" for r in imported)
    assert all(r["scope"] == dates for r in imported)


@pytest.mark.asyncio
async def test_historical_freeze_reconstructs_missing_scope_without_editing_frozen_identity(db_session, test_user):
    project = await create_project(db_session, test_user.id, name="Old freeze")
    legacy = {"trial_key": "legacy:old:A", "task_id": "old", "attempt_id": "A", "expression_key": "close"}
    old = await build_trial_manifest(db_session, project.id, [], legacy_trials=[legacy])
    frozen = {"final_evaluation_id": "se_test", "selection_evaluation_id": "se_select", "trial_manifest": old}
    row = ResearchRound(project_id=project.id, coordinator_id=test_user.id, coordinator_digest="test",
        status="completed", final_evaluation_id="se_test", selection_config={"scope": {"market": "us", "universe_id": "demo"},
        "data_inputs": []}, holdout={"start": "2024-05-21", "end": "2024-09-09"}, frozen={
            **frozen, "freeze_hash": content_hash("quantgpt.round_freeze/v1", frozen)})
    db_session.add(row)
    await db_session.flush()
    db_session.add(ResearchDirection(round_id=row.id, name="trend", hypothesis="trend", actor_id=test_user.id,
        credential_digest="old", legacy_sources=[{"task_id": "old", "trials": [legacy]}]))
    await db_session.flush()

    async def revised():
        return (await final_validation_context(db_session, project.id, "se_test"))["trial_manifest"]

    unseen = await revised()
    assert unseen["legacy_window_review"]["status"] == "unverified"
    assert unseen["observed_trials"] == 1 and unseen["original_manifest_hash"] == old["manifest_hash"]
    db_session.add(Task(id="old", user_id=test_user.id, task_type="iteration", status="iteration_completed",
        params={"market": "us", "universe": "demo", "start_date": "2024-05-21", "end_date": "2024-09-09"},
        result={"search_attempts": [{"id": "A", "status": "success", "expression": "close", "raw_score": 0}]}))
    await db_session.flush()
    exposed = await revised()
    assert exposed["legacy_window_review"]["overlapping_or_later_trials"] == [legacy["trial_key"]]
    assert exposed["manifest_hash"] != unseen["manifest_hash"]
    assert (await db_session.scalar(select(ResearchRound.frozen)))["trial_manifest"] == old


def test_date_scope_distinguishes_disjoint_data_from_unknown_history():
    config = {"scope": {"market": "us_equity", "universe_id": "large"}, "data_inputs": [{"manifest_id": "fixture"}]}
    holdout = {"start": "2024-05-21", "end": "2024-09-09"}

    def status(**scope):
        return review_legacy_windows([{"trial_key": "legacy", "scope": scope}], config, holdout)["status"]

    assert status(start_date="2023-01-01", end_date="2023-02-02") == "clear"
    assert status(end_date="2023-02-02") == "clear"
    assert status(end_date="2024-07-01") == "unverified"
    assert status(start_date="2024-05-21", end_date="2024-09-09") == "unverified"
    assert status(start_date="2024-05-21", end_date="2024-09-09", manifest_id="fixture") == "exposed"
    assert status(start_date="2024-05-21", end_date="2024-09-09", market="us") == "exposed"
    assert status(start_date="2024-05-21", end_date="2024-09-09", market="custom_venue") == "unverified"
    assert status(start_date="2024-05-21", end_date="2024-09-09", market="a_share") == "clear"
    assert status(start_date="2024-09-10", end_date="2024-09-20", market="us_equity") == "exposed"


@pytest.mark.asyncio
async def test_partial_history_preserves_verified_zero_score_and_flags_the_source(db_session, test_user):
    from quantgpt.research.legacy_history import round_legacy_trials

    project = await create_project(db_session, test_user.id, name="Incomplete archive")
    archived = Task(id="partial", user_id=test_user.id, task_type="iteration", status="iteration_completed",
        params={"start_date": "2023-01-01", "end_date": "2023-12-01"}, result={"search_attempts": [
            {"id": "A", "expression": "close", "status": "success", "raw_score": 0},
            {"id": "B", "expression": "open", "status": "success", "raw_score": None}]})
    db_session.add(archived)
    await db_session.flush()
    with pytest.raises(IncompleteIterationHistory) as exc:
        await load_observed_iteration_trials(db_session, test_user.id, "partial", project_id=project.id)
    assert [t["attempt_id"] for t in exc.value.trials] == ["A"]
    branch = ResearchDirection(actor_id=test_user.id, legacy_sources=[{"task_id": "partial", "trials": []}])
    missing = set()
    trials = await round_legacy_trials(db_session, [branch], project.id, unresolved_sources=missing)
    assert missing == {"partial"} and trials == exc.value.trials
    review = review_legacy_windows(trials, {"scope": {"market": "us", "universe_id": "stocks"}, "data_inputs": []},
        {"start": "2024-01-01", "end": "2024-09-30"}, unresolved_sources=missing)
    assert review["status"] == "unverified" and review["incomplete_sources"] == ["partial"]


@pytest.mark.asyncio
async def test_historical_pass_is_archived_and_excluded_from_current_profile(db_session, test_user, client,
                                                                           auth_headers, tmp_path, monkeypatch):
    from quantgpt.research.evaluations import get_evaluation, read_artifact, write_artifact
    from quantgpt.research.validation import POLICY_VERSION, evaluate_profile
    from quantgpt.research.validation_runner import run_local_validation
    from quantgpt.task_store import load_durable_task
    from tests.test_offline_validation import _demo, _evaluate

    root, project, base, demo = await _demo(client, auth_headers, tmp_path, monkeypatch)
    factor = await _evaluate(client, auth_headers, base, demo)
    eid = factor["evaluation_id"]
    old = {"policy": "offline_validation/v2", "report_key": "old", "evaluation_id": eid,
           "evaluation_hash": factor["evaluation_hash"], "profile": "local_factor",
           "checks": {"oos": {"status": "passed"}}}
    row = await get_evaluation(db_session, test_user.id, project, eid)
    ref = await write_artifact(db_session, row, "offline_validation", old)
    db_session.add(Task(id="old-report", user_id=test_user.id, project_id=project, task_type="research_evaluation",
                        status="completed", params={"kind": "validation"}, result=old))
    await db_session.commit()
    artifact = await read_artifact(db_session, test_user.id, project, uuid.UUID(ref["artifact_id"]))
    task = await load_durable_task(db_session, "old-report", str(test_user.id))
    assert artifact["payload"] == task["result"] == old
    assert artifact["validation_status"] == task["validation_status"]
    current = await evaluate_profile(db_session, test_user.id, project, eid, profile="local_factor")
    assert "OFFLINE_VALIDATION_REPLAY_REQUIRED" in current["blockers"]
    assert current["offline_validation"] is None
    replay = await run_local_validation(db_session, test_user.id, project, eid, snapshot_root=root)
    assert replay["policy"] == POLICY_VERSION and replay["checks"]["oos"]["status"] == "failed"
    refreshed = await evaluate_profile(db_session, test_user.id, project, eid, profile="local_factor")
    assert refreshed["offline_validation"]["report_key"] == replay["report_key"]
