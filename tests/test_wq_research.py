"""Mocked remote uncertainty, durable evidence and project authorization."""

import asyncio
import uuid
from unittest.mock import MagicMock

import pytest
import requests
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from quantgpt.models import Experiment, ExperimentArtifact, ProjectMember
from quantgpt.research.contracts import (
    US_RESEARCH_FIELDS,
    DataInputRef,
    EngineIdentity,
    EvaluationConfigV1,
    EvaluationWindow,
    FactorDefinitionV1,
    ResearchScope,
)
from quantgpt.research.evaluations import read_artifact
from quantgpt.research.projects import ProjectAccessError, create_project
from quantgpt.research.wq_backend import (
    cancel_wq_evaluation,
    capability_report,
    evaluate_wq,
    normalize_wq_config,
    reconcile_wq_evaluation,
)
from quantgpt.wq_brain_client import API_BASE, WQBrainClient
from quantgpt.wq_brain_service import run_single_simulation

REMOTE_REF = API_BASE + "/simulations/run-123"


def _config():
    return EvaluationConfigV1(
        backend="wq", direction="higher_is_better",
        scope=ResearchScope(market="us_equity", currency="USD", universe_id="TOP3000",
                            universe_mode="dynamic_pit", universe_version="unknown", calendar_id="platform",
                            calendar_version="unknown", timezone="America/New_York", benchmark="platform"),
        window=EvaluationWindow(start_session="2024-01-01", end_session="2024-12-31", phase="platform",
                                split_id="platform", split_hash="a" * 64),
        data_inputs=(DataInputRef(source="WQ", feed="platform", adjustment="unknown", asof="2025-01-01T00:00:00Z"),),
        engine=EngineIdentity(engine="wq", version="unknown", code_version="unknown"),
        remote_settings={"region": "USA", "universe": "TOP3000"},
    )


def _definition():
    return FactorDefinitionV1(expression="rank(close)", language="wq",
                              fields=(next(item for item in US_RESEARCH_FIELDS if item.name == "close"),))


def _client():
    client = MagicMock(spec=WQBrainClient)
    client.start_simulation.return_value = {"ok": True, "remote_run_ref": REMOTE_REF, "simulation_id": "run-123"}
    client.poll_simulation.return_value = {
        "ok": True, "status": "completed", "remote_run_ref": REMOTE_REF,
        "simulation_id": "run-123", "alpha_id": "alpha-123", "is": {"sharpe": 1.3}, "oos": {"sharpe": 0.7},
        "raw_platform_result": {"status": "COMPLETE", "alpha": "alpha-123", "unmodeled_field": "preserved"},
    }
    return client


def test_lost_post_response_is_not_retried_and_transport_does_not_retry_connections():
    client = WQBrainClient("a", "b")
    actual = client._get_session()
    retries = actual.get_adapter(API_BASE).max_retries
    assert retries.connect == 0 and "POST" not in retries.allowed_methods
    client.close()
    client._session = MagicMock()
    client._session.post.side_effect = requests.Timeout("response lost after remote accepted")
    result = client.simulate("rank(close)")
    assert result["status"] == "remote_outcome_unknown" and result["retryable"] is False
    client._session.post.assert_called_once()
    client._session.get.assert_not_called()


@pytest.mark.parametrize(("http_status", "state"), [(401, "failed"), (429, "failed"), (503, "remote_outcome_unknown")])
def test_remote_rejections_preserve_known_vs_unknown(http_status, state):
    client = WQBrainClient("a", "b")
    client._session = MagicMock()
    client._session.post.return_value = MagicMock(status_code=http_status, text="platform response")
    result = client.start_simulation("close")
    assert result["status"] == state
    assert result["retryable"] is (http_status == 429)
    client._session.post.assert_called_once()


@pytest.mark.parametrize("location", ["", "https://other.example/simulations/1", "//other.example/simulations/1"])
def test_accepted_request_without_safe_reference_is_unknown(location):
    client = WQBrainClient("a", "b")
    client._session = MagicMock()
    client._session.post.return_value = MagicMock(status_code=201, headers={"Location": location})
    assert client.start_simulation("close")["status"] == "remote_outcome_unknown"
    client._session.get.assert_not_called()


def test_poll_resumes_without_post_and_preserves_unknown_reference():
    client = WQBrainClient("a", "b")
    client._session = MagicMock()
    client._session.get.side_effect = requests.Timeout()
    result = client.poll_simulation(REMOTE_REF, max_polls=1)
    assert result["status"] == "remote_outcome_unknown" and result["remote_run_ref"] == REMOTE_REF
    client._session.post.assert_not_called()


def test_submission_never_reposts_on_lost_response_or_ambiguous_checks(monkeypatch):
    monkeypatch.setattr("quantgpt.wq_brain_client.time.sleep", lambda _: None)
    client = WQBrainClient("a", "b")
    client._session = MagicMock()
    client._session.post.side_effect = requests.ConnectionError("unknown")
    assert client.submit_alpha("abc")["status"] == "remote_outcome_unknown"
    client._session.post.assert_called_once()
    client._session.post.reset_mock(side_effect=True)
    client._session.get.return_value = MagicMock(status_code=200)
    client._session.get.return_value.json.return_value = {
        "status": "UNSUBMITTED", "is": {"checks": [{"name": "SELF_CORRELATION", "result": "PASS"}]},
    }
    result = client._poll_alpha_submission("abc", max_polls=2, interval=0)
    assert result["ok"] is False and result["status"] == "remote_outcome_unknown"
    client._session.post.assert_not_called()


def test_service_preserves_remote_uncertainty():
    client = _client()
    client.simulate.return_value = {"ok": False, "status": "remote_outcome_unknown", "remote_run_ref": REMOTE_REF}
    result = run_single_simulation(client, "close")
    assert result["status"] == "remote_outcome_unknown" and result["remote_run_ref"] == REMOTE_REF


@pytest.mark.asyncio
async def test_platform_evidence_durable_and_idempotent_without_local_promotion(db_session, test_user):
    project = await create_project(db_session, test_user.id, name="Remote")
    client = _client()
    output = await evaluate_wq(db_session, test_user.id, project.id, _definition(), _config(), client=client)
    again = await evaluate_wq(db_session, test_user.id, project.id, _definition(), _config(), client=client)
    assert output["evaluation_id"] == again["evaluation_id"] and output["status"] == "completed"
    assert output["evidence_status"] == "platform_only"
    summary = output["summary"]
    assert summary["local_strategy_eligible"] is False and summary["independent_final_validation"] is False
    assert summary["data_version_status"] == "unknown" and summary["remote_run_ref"] == REMOTE_REF
    artifact = await read_artifact(db_session, test_user.id, project.id, uuid.UUID(summary["artifact_ref"]["artifact_id"]))
    assert artifact["payload"]["platform_result"]["raw_platform_result"]["unmodeled_field"] == "preserved"
    assert artifact["payload"]["window_control_verified"] is False
    client.start_simulation.assert_called_once()
    client.poll_simulation.assert_called_once()


@pytest.mark.asyncio
async def test_lost_remote_response_blocks_blind_retry_and_keeps_request(db_session, test_user):
    project = await create_project(db_session, test_user.id, name="Unknown")
    client = _client()
    client.start_simulation.return_value = {"ok": False, "status": "remote_outcome_unknown", "error": "lost response"}
    first = await evaluate_wq(db_session, test_user.id, project.id, _definition(), _config(), client=client)
    await evaluate_wq(db_session, test_user.id, project.id, _definition(), _config(), client=client)
    resumed = await reconcile_wq_evaluation(db_session, test_user.id, project.id, first["evaluation_id"], client=client)
    assert resumed["status"] == "reconciliation_required"
    assert resumed["summary"]["request_fingerprint"] and resumed["summary"]["request_ref"]
    assert resumed["summary"]["retryable"] is False
    client.start_simulation.assert_called_once()
    client.poll_simulation.assert_not_called()


@pytest.mark.asyncio
async def test_remote_reference_saved_before_poll_and_recovery_only_gets(db_session, test_user):
    project = await create_project(db_session, test_user.id, name="Recovery")
    client = _client()
    good = client.poll_simulation.return_value
    client.poll_simulation.return_value = {"ok": False, "status": "remote_outcome_unknown", "error": "poll timeout"}
    actor_id = test_user.id
    first = await evaluate_wq(db_session, test_user.id, project.id, _definition(), _config(), client=client)
    db_session.expire_all()  # Simulate losing the in-memory ORM/cache state.
    project_id = uuid.UUID(first["project_id"])
    client.poll_simulation.return_value = good
    resumed = await reconcile_wq_evaluation(db_session, actor_id, project_id, first["evaluation_id"], client=client)
    assert resumed["status"] == "completed" and resumed["summary"]["remote_run_ref"] == REMOTE_REF
    client.start_simulation.assert_called_once()
    assert client.poll_simulation.call_count == 2


@pytest.mark.asyncio
async def test_cancel_reports_only_local_wait_and_requires_explicit_reconcile(db_session, test_user):
    project = await create_project(db_session, test_user.id, name="Cancel")
    client = _client()
    client.poll_simulation.return_value = {"ok": False, "status": "remote_outcome_unknown"}
    first = await evaluate_wq(db_session, test_user.id, project.id, _definition(), _config(), client=client)
    cancelled = await cancel_wq_evaluation(db_session, test_user.id, project.id, first["evaluation_id"])
    assert cancelled["status"] == "local_wait_cancelled"
    assert cancelled["summary"]["remote_cancel_confirmed"] is False
    client.poll_simulation.return_value = {"ok": False, "status": "remote_cancel_confirmed"}
    confirmed = await reconcile_wq_evaluation(db_session, test_user.id, project.id, first["evaluation_id"], client=client)
    assert confirmed["status"] == "remote_cancel_confirmed" and confirmed["summary"]["remote_cancel_confirmed"]
    client.start_simulation.assert_called_once()


@pytest.mark.asyncio
async def test_cancel_check_discards_success_and_revoked_members_cannot_recover(db_session, test_user):
    project = await create_project(db_session, test_user.id, name="Cancel flag")
    project_id, actor_id = project.id, test_user.id
    client = _client()
    cancelled = False

    def poll(*args, **kwargs):
        nonlocal cancelled
        cancelled = True
        return {"ok": True, "status": "completed", "is": {"sharpe": 999}}

    client.poll_simulation.side_effect = poll
    result = await evaluate_wq(db_session, actor_id, project_id, _definition(), _config(),
                               client=client, cancel_check=lambda: cancelled)
    assert result["status"] == "local_wait_cancelled"
    assert "is_metrics" not in result["summary"]
    member = await db_session.get(ProjectMember, (project_id, actor_id))
    await db_session.delete(member)
    await db_session.commit()
    with pytest.raises(ProjectAccessError):
        await reconcile_wq_evaluation(db_session, actor_id, project_id, result["evaluation_id"], client=client)


def test_config_scope_and_unknown_capabilities_cannot_be_forged():
    config = _config()
    config = config.model_copy(update={"data_inputs": (
        config.data_inputs[0].model_copy(update={"version_status": "verified", "manifest_id": "forged",
                                                "content_sha256": "b" * 64}),
    )})
    normalized = normalize_wq_config(config)
    assert normalized.data_inputs[0].version_status == "unknown"
    assert normalized.engine.conformance == "unverified"
    assert capability_report()["external_integration"] == "not_run"
    mismatch = config.model_copy(update={"remote_settings": {"universe": "TOP500"}})
    with pytest.raises(ValueError, match="UNIVERSE_SCOPE_MISMATCH"):
        normalize_wq_config(mismatch)


@pytest.mark.asyncio
async def test_durable_request_precedes_remote_side_effect(db_session, test_user):
    project = await create_project(db_session, test_user.id, name="Acceptance")
    client = _client()
    # A transport fault cannot remove the committed request or create a second study.
    client.start_simulation.side_effect = RuntimeError("transport died")
    output = await evaluate_wq(db_session, test_user.id, project.id, _definition(), _config(), client=client)
    assert output["status"] == "remote_outcome_unknown"
    experiments = (await db_session.scalars(select(Experiment))).all()
    artifacts = (await db_session.scalars(select(ExperimentArtifact))).all()
    assert len(experiments) == 1 and {item.artifact_type for item in artifacts} == {"remote_request", "remote_evidence"}


@pytest.mark.asyncio
async def test_saved_platform_checks_pass_own_profile_but_never_local_strategy(db_session, test_user):
    from quantgpt.research.validation import evaluate_profile

    project = await create_project(db_session, test_user.id, name="Platform scope")
    client = _client()
    client.poll_simulation.return_value["is"]["checks"] = [
        {"name": "LOW_SHARPE", "result": "PASS"}, {"name": "LOW_FITNESS", "result": "PASS"},
    ]
    result = await evaluate_wq(db_session, test_user.id, project.id, _definition(), _config(), client=client)
    remote = await evaluate_profile(db_session, test_user.id, project.id, result["evaluation_id"], profile="wq_remote")
    local = await evaluate_profile(db_session, test_user.id, project.id, result["evaluation_id"], profile="local_strategy")
    assert remote["allowed"] is True
    assert local["allowed"] is False and "EVIDENCE_SCOPE_MISMATCH" in local["blockers"]


@pytest.mark.asyncio
async def test_exception_style_task_cancellation_never_starts_remote_post(db_session, test_user):
    from quantgpt.task_store import CancelledException

    project = await create_project(db_session, test_user.id, name="Cancelled before POST")
    client = _client()

    def check():
        raise CancelledException("cancel requested")

    result = await evaluate_wq(db_session, test_user.id, project.id, _definition(), _config(), client=client,
                               cancel_check=check)
    assert result["status"] == "cancelled"
    client.start_simulation.assert_not_called()


@pytest.mark.asyncio
async def test_cancel_during_post_preserves_remote_reference_without_success(db_session, test_user):
    from quantgpt.task_store import (
        cancel_durable_task,
        check_cancelled,
        claim_durable_task,
        load_durable_task,
        submit_durable_task,
    )

    project = await create_project(db_session, test_user.id, name="Cancel during POST")
    task, _ = await submit_durable_task(db_session, actor_id=str(test_user.id), project_id=str(project.id),
        task_type="research_evaluation", params={"kind": "factor", "config": _config().model_dump(mode="json")})
    context = await claim_durable_task(db_session, task["task_id"])
    client = _client()
    accepted = client.start_simulation.return_value
    loop = asyncio.get_running_loop()

    def start(*args, **kwargs):
        asyncio.run_coroutine_threadsafe(cancel_durable_task(db_session, task["task_id"], str(test_user.id)), loop).result()
        return accepted

    client.start_simulation.side_effect = start
    result = await evaluate_wq(db_session, test_user.id, project.id, _definition(), _config(), client=client,
                               cancel_check=lambda: check_cancelled(task["task_id"]), task_context=context)
    durable = await load_durable_task(db_session, task["task_id"], str(test_user.id))
    assert result["status"] == durable["status"] == "local_wait_cancelled"
    assert durable["remote_run_ref"] == REMOTE_REF and durable["result"] is None
    assert "is_metrics" not in result["summary"]


@pytest.mark.asyncio
async def test_research_worker_does_not_mark_unknown_remote_result_completed(db_session, test_user, engine, monkeypatch):
    from quantgpt import db
    from quantgpt.research.jobs import run_research_job
    from quantgpt.task_store import claim_durable_task, load_durable_task, submit_durable_task

    project = await create_project(db_session, test_user.id, name="Remote worker result")
    task, _ = await submit_durable_task(db_session, actor_id=str(test_user.id), project_id=str(project.id),
        task_type="research_evaluation", params={"kind": "factor", "config": _config().model_dump(mode="json")})
    context = await claim_durable_task(db_session, task["task_id"])
    monkeypatch.setattr(db, "_get_session_factory", lambda: async_sessionmaker(engine, expire_on_commit=False))

    async def unknown(*args, **kwargs):
        return {"status": "remote_outcome_unknown", "summary": {"remote_run_ref": REMOTE_REF, "next_action": "resume_polling"}}

    monkeypatch.setattr("quantgpt.research.jobs.execute_research_request", unknown)
    await asyncio.to_thread(run_research_job, context)
    db_session.expire_all()
    durable = await load_durable_task(db_session, task["task_id"], context["user_id"])
    assert durable["status"] == "remote_outcome_unknown" and durable["remote_run_ref"] == REMOTE_REF
    assert durable["retryable"] is False
