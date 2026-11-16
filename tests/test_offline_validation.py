"""Actual frozen-panel replay, immutable evidence, and conservative acceptance."""

import copy
import hashlib
import uuid

import pandas as pd
import pytest
from sqlalchemy import func, select

from quantgpt.models import ExperimentArtifact, Task
from quantgpt.research.jobs import execute_research_request
from quantgpt.research.validation_runner import _series, run_local_validation


async def _demo(client, headers, tmp_path, monkeypatch):
    root = tmp_path / "snapshots"
    monkeypatch.setenv("QUANTGPT_RESEARCH_SNAPSHOT_ROOT", str(root))
    created = await client.post("/api/v1/research/projects", headers=headers, json={"name": "Validation"})
    assert created.status_code == 201, created.text
    project_id = uuid.UUID(created.json()["id"])
    base = f"/api/v1/research/projects/{project_id}"
    response = await client.post(base + "/offline-demo", headers=headers)
    assert response.status_code == 200, response.text
    return root, project_id, base, response.json()


async def _evaluate(client, headers, base, demo):
    response = await client.post(base + "/evaluations", headers=headers,
                                json={"definitions": demo["definitions"], "config": demo["config"]})
    assert response.status_code == 200, response.text
    row = response.json()["evaluations"][0]
    assert row["summary"]["performance_observed"], row
    return row


@pytest.mark.asyncio
async def test_only_current_operator_evidence_is_usable_for_validation_and_export(
    client, auth_headers, db_session, test_user, tmp_path, monkeypatch,
):
    catalog = tmp_path / "operator-evidence"
    monkeypatch.setenv("QUANTGPT_RESEARCH_EVIDENCE_ROOT", str(catalog))
    root, project, base, demo = await _demo(client, auth_headers, tmp_path, monkeypatch)
    snapshot = demo["config"]["data_inputs"][0]["manifest_id"]
    eid = (await _evaluate(client, auth_headers, base, demo))["evaluation_id"]
    path = catalog / snapshot / "evidence.json"
    path.parent.mkdir(parents=True)
    api = base + f"/evaluations/{eid}"
    first = await run_local_validation(db_session, test_user.id, project, eid, snapshot_root=root)
    assert first["external_acceptance"]["status"] == "not_run"
    key = {"idempotency_key": "v" * 160}
    before = await client.post(api + "/validation-runs", headers=auth_headers, json=key)
    assert before.status_code == 202, before.text
    path.write_bytes(b"{incomplete")
    stale = (await client.get(api + "/validation", headers=auth_headers)).json()
    assert stale["offline_validation"] is None and "OFFLINE_VALIDATION_REPLAY_REQUIRED" in stale["blockers"]
    with pytest.raises(ValueError, match="EXTERNAL_EVIDENCE_CHANGED"):
        await run_local_validation(db_session, test_user.id, project, eid, snapshot_root=root,
            task_context={"params": {"external_evidence_revision": "not_supplied"}})
    after = await client.post(api + "/validation-runs", headers=auth_headers, json=key)
    assert after.status_code == 202 and after.json()["task_id"] != before.json()["task_id"]
    task = await db_session.get(Task, after.json()["task_id"])
    assert task.params["external_evidence_revision"] == hashlib.sha256(path.read_bytes()).hexdigest()
    second = await run_local_validation(db_session, test_user.id, project, eid, snapshot_root=root)
    assert second["external_acceptance"]["status"] == "failed"
    assert "EXTERNAL_EVIDENCE_INVALID_JSON" in second["external_acceptance"]["blockers"]
    assert second["report_key"] != first["report_key"]
    current = (await client.get(api + "/validation", headers=auth_headers)).json()
    assert not current["allowed"] and current["external_acceptance"] == second["external_acceptance"]
    path.unlink()
    restored = (await client.get(api + "/validation", headers=auth_headers)).json()
    assert restored["offline_validation"]["report_key"] == first["report_key"] and not restored["allowed"]


@pytest.mark.asyncio
async def test_validation_http_acceptance_replay_retry_and_integrity(client, auth_headers, db_session,
                                                                  test_user, tmp_path, monkeypatch):
    root, project, base, demo = await _demo(client, auth_headers, tmp_path, monkeypatch)
    evaluation = await _evaluate(client, auth_headers, base, demo)
    eid = evaluation["evaluation_id"]
    url = base + f"/evaluations/{eid}/validation-runs"
    submitted = await client.post(url, headers=auth_headers, json={"idempotency_key": "validate-once"})
    assert submitted.status_code == 202, submitted.text
    task = await db_session.get(Task, submitted.json()["task_id"])
    assert task.task_type == "research_evaluation"
    assert task.params["server_config_frozen"] is True
    assert task.params["kind"] == "validation"
    repeated = await client.post(url, headers=auth_headers, json={"idempotency_key": "validate-once"})
    assert repeated.json()["task_id"] == submitted.json()["task_id"]
    assert repeated.json()["created"] is False
    forged = await client.post(url, headers=auth_headers, json={"checks": {"oos": {"status": "passed"}}})
    assert forged.status_code == 422

    request = {"kind": "validation", "evaluation_id": eid}
    report = await execute_research_request(db_session, test_user.id, project, request)
    assert report["status"] == "completed"
    assert report["allowed"] is False
    for name in ("simulation", "data_quality", "costs", "definition_config", "label_boundaries", "replayable_data"):
        assert report["checks"][name]["status"] == "passed", report["checks"]
    assert report["checks"]["oos"]["status"] == "failed"
    assert report["checks"]["walk_forward"]["status"] == "insufficient_data"
    assert report["checks"]["placebo"]["status"] == "diagnostic"
    assert report["data_contract"]["snapshot_integrity"] == "verified"
    assert len(report["data_contract"]["required_external_evidence"]) == 4
    again = await run_local_validation(db_session, test_user.id, project, eid, snapshot_root=root)
    assert again == report
    assert await db_session.scalar(select(func.count()).select_from(ExperimentArtifact).where(
        ExperimentArtifact.experiment_id == eid, ExperimentArtifact.artifact_type == "offline_validation")) == 1
    original = await client.get(base + f"/evaluations/{eid}", headers=auth_headers)
    assert original.json()["summary"] == evaluation["summary"]
    profile = await client.get(base + f"/evaluations/{eid}/validation", headers=auth_headers)
    assert profile.status_code == 200, profile.text
    assert not profile.json()["allowed"]
    assert "EXTERNAL_DATA_ACCEPTANCE_REQUIRED" in profile.json()["blockers"]
    ref = profile.json()["offline_validation"]
    fetched = await client.get(base + f"/artifacts/{ref['artifact_id']}", headers=auth_headers)
    assert fetched.json()["payload"] == report

    # A new observed candidate makes the prior trial correction stale.
    demo["definitions"][0]["expression"] = "-rank(close)"
    await _evaluate(client, auth_headers, base, demo)
    profile = await client.get(base + f"/evaluations/{eid}/validation", headers=auth_headers)
    assert "OFFLINE_VALIDATION_STALE_TRIAL_COUNT" in profile.json()["blockers"]
    from quantgpt.research.projects import ProjectAccessError
    from quantgpt.task_store import CancelledException

    with pytest.raises(ProjectAccessError):
        await run_local_validation(db_session, uuid.uuid4(), project, eid, snapshot_root=root)

    def cancel():
        raise CancelledException("cancelled")

    with pytest.raises(CancelledException):
        await run_local_validation(db_session, test_user.id, project, eid, snapshot_root=root, cancel_check=cancel)
    assert await db_session.scalar(select(func.count()).select_from(ExperimentArtifact).where(
        ExperimentArtifact.experiment_id == eid, ExperimentArtifact.artifact_type == "offline_validation")) == 1
    artifact = await db_session.get(ExperimentArtifact, uuid.UUID(ref["artifact_id"]))
    artifact.payload = {**artifact.payload, "allowed": True}
    await db_session.commit()
    with pytest.raises(ValueError, match="integrity check failed"):
        await _read_tampered(db_session, test_user.id, project, artifact.id)


async def _read_tampered(session, actor, project, artifact_id):
    from quantgpt.research.evaluations import read_artifact

    return await read_artifact(session, actor, project, artifact_id)


@pytest.mark.asyncio
async def test_final_validation_uses_same_selected_candidate_and_reruns_folds(client, auth_headers, db_session,
                                                                           test_user, tmp_path, monkeypatch):
    root, project, base, demo = await _demo(client, auth_headers, tmp_path, monkeypatch)
    sessions = pd.bdate_range("2024-01-02", periods=180)
    start, end = sessions[100].date().isoformat(), sessions[-1].date().isoformat()
    holdout = await client.post(base + "/holdout", headers=auth_headers, json={"start": start, "end": end})
    assert holdout.status_code == 200, holdout.text
    demo["config"]["window"]["end_session"] = sessions[94].date().isoformat()
    selected = await _evaluate(client, auth_headers, base, demo)
    final = copy.deepcopy(demo)
    final["config"]["window"].update(phase="final", start_session=start, end_session=end)
    evaluated = await _evaluate(client, auth_headers, base, final)
    report = await run_local_validation(db_session, test_user.id, project, evaluated["evaluation_id"],
        snapshot_root=root, selection_evaluation_id=selected["evaluation_id"])
    assert report["checks"]["oos"]["status"] == "passed"
    folds = report["checks"]["walk_forward"]
    assert folds["method"] == "frozen_candidate_forward_replay" and not folds["adaptive_retraining"]
    assert len(folds["windows"]) == 3
    assert sum(fold["sessions"] for fold in folds["windows"]) == 80
    assert all(fold["sessions"] >= 20 for fold in folds["windows"])
    assert report["allowed"] is False and report["external_acceptance"]["status"] == "not_run"
    # A second caller can request an unpaired diagnostic. Its arrival cannot
    # overwrite the current paired evidence, even when the paired retry is cached.
    from quantgpt.models import User
    from quantgpt.research.projects import set_project_member
    from quantgpt.research.validation import evaluate_profile

    member = User(id=uuid.uuid4(), email="validator@example.com", is_active=True)
    db_session.add(member)
    await db_session.flush()
    await set_project_member(db_session, test_user.id, project, member.id, "researcher")
    await db_session.commit()
    unpaired = await run_local_validation(db_session, member.id, project, evaluated["evaluation_id"], snapshot_root=root)
    assert unpaired["checks"]["oos"]["status"] == "failed"
    again = await run_local_validation(db_session, test_user.id, project, evaluated["evaluation_id"],
        snapshot_root=root, selection_evaluation_id=selected["evaluation_id"])
    profile = await evaluate_profile(db_session, member.id, project, evaluated["evaluation_id"], profile="local_factor")
    assert profile["offline_validation"]["report_key"] == again["report_key"] == report["report_key"]
    assert profile["checks"]["oos"]["status"] == "passed"
    demo["definitions"][0]["expression"] = "-rank(close)"
    wrong = await _evaluate(client, auth_headers, base, demo)
    with pytest.raises(ValueError, match="SELECTION_LINEAGE_MISMATCH"):
        await run_local_validation(db_session, test_user.id, project, evaluated["evaluation_id"],
            snapshot_root=root, selection_evaluation_id=wrong["evaluation_id"])
    # Historical dates alone cannot prove selection before seeing the holdout.
    demo["definitions"] = copy.deepcopy(final["definitions"])
    demo["config"]["window"]["end_session"] = sessions[93].date().isoformat()
    late_selection = await _evaluate(client, auth_headers, base, demo)
    with pytest.raises(ValueError, match="SELECTION_NOT_FROZEN_BEFORE_FINAL_EXPOSURE"):
        await run_local_validation(db_session, test_user.id, project, evaluated["evaluation_id"],
            snapshot_root=root, selection_evaluation_id=late_selection["evaluation_id"])


@pytest.mark.parametrize("records", [[], [{"session": "2024-01-02", "return": float("nan")}],
    [{"session": "2024-01-02", "return": -1.1}],
    [{"session": "2024-01-03", "return": 0}, {"session": "2024-01-02", "return": 0}],
    [{"session": "2024-01-02", "return": 0}, {"session": "2024-01-02", "return": 0}]])
def test_invalid_saved_return_series_rejected(records):
    with pytest.raises(ValueError, match="RETURN_ARTIFACT"):
        _series(records)
