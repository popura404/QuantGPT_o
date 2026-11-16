"""A round counts observed calculations once across recorded research sources."""

import uuid

import pytest

from quantgpt.experiment_ledger import record_experiment
from quantgpt.models import ResearchDirection, ResearchRound, Task
from quantgpt.research.evaluations import write_artifact
from quantgpt.research.projects import create_project
from quantgpt.research.validation import build_trial_manifest, observed_trial_count
from quantgpt.search_ledger import load_observed_iteration_trials


@pytest.mark.asyncio
async def test_round_manifest_counts_all_observations_of_imported_task(db_session, test_user):
    project = await create_project(db_session, test_user.id, name="Trial accounting")
    task = Task(id="legacy-iter", user_id=test_user.id, task_type="iteration", status="iteration_completed",
                result={"search_attempts": [
                    {"id": "A", "expression": "rank(close)", "status": "success", "raw_score": 10},
                    {"id": "B", "expression": "rank(open)", "status": "success", "raw_score": 0,
                     "entered_next_round": False},
                    {"id": "C", "expression": "rank(", "status": "failed", "failed": True, "raw_score": 0}],
                    "candidates": [{"search_attempt": {"id": "A"}, "expression": "rank(close)",
                                    "status": "success", "score": 10}]})
    db_session.add(task)
    await db_session.flush()
    legacy = await load_observed_iteration_trials(db_session, test_user.id, str(task.id))
    assert {r["attempt_id"] for r in legacy} == {"A", "B"}
    with pytest.raises(ValueError, match="ITERATION_TASK_NOT_FOUND"):
        await load_observed_iteration_trials(db_session, uuid.uuid4(), str(task.id))
    rows = []
    for name, observed in (("one", True), ("two", True), ("failed", False), ("later", True)):
        row = await record_experiment(db_session, expression=name, params={}, user_id=test_user.id)
        row.project_id, row.evaluation_hash = project.id, name
        row.result_summary = {"performance_observed": observed}
        rows.append(row)
    await db_session.flush()
    expected = [r.experiment_id for r in rows[:3]]
    params = {"risk_aversion": 2, "performance_observed": True,
              "cost_sensitivity": [{"cost_bps": 0}, {"cost_bps": 5}]}
    for key, grid in (("first", [2]), ("second", [2, 3])):
        await write_artifact(db_session, rows[0], "portfolio_optimization", {
            "portfolio_hash": key, "evaluation_id": rows[0].experiment_id,
            "signal_ref": {"content_sha256": "fixed-signal"},
            "config": {"transaction_cost_bps": 5, "risk_aversion_candidates": grid},
            "parameter_trials": [params]})
    frozen = await build_trial_manifest(db_session, project.id, expected + expected, legacy_trials=legacy + legacy)
    assert frozen["evaluation_trials"] == frozen["portfolio_trials"] == frozen["legacy_trials"] == 2
    assert frozen["observed_trials"] == 6
    assert frozen["unobserved_evaluation_ids"] == [rows[2].experiment_id]
    assert frozen == await build_trial_manifest(db_session, project.id, reversed(expected), legacy_trials=legacy)
    row = ResearchRound(project_id=project.id, coordinator_id=test_user.id, coordinator_digest="test",
                        selection_config={}, holdout={})
    db_session.add(row)
    await db_session.flush()
    for name in ("trend", "reversal"):
        db_session.add(ResearchDirection(round_id=row.id, name=name, hypothesis=name, actor_id=test_user.id,
            credential_digest=name, legacy_sources=[{"task_id": str(task.id), "trials": legacy}]))
    await db_session.flush()
    # The active dashboard includes the later observation and imports only once.
    assert await observed_trial_count(db_session, project.id) == frozen["observed_trials"] + 1 == 7
    other = await create_project(db_session, test_user.id, name="Other project")
    with pytest.raises(ValueError, match="ROUND_EVALUATION_NOT_FOUND"):
        await build_trial_manifest(db_session, other.id, expected)
