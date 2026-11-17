import pandas as pd
import pytest
from sqlalchemy import func, select

from quantgpt.models import Experiment, FinalWindowExposure, ResearchRound, Task
from quantgpt.research.preparation import prepare_project_demo
from quantgpt.research.projects import create_project
from quantgpt.research.window_policy import plan_session_windows


@pytest.mark.parametrize(("holding", "purge", "expected_gap"), [(1, 1, 5), (21, 5, 21), (5, 30, 30)])
def test_exact_history_leaves_complete_trading_days_between_scored_windows(holding, purge, expected_gap):
    required = 30 + 756 + expected_gap + 252
    sessions = pd.bdate_range("2018-01-02", periods=required)
    plan = plan_session_windows(sessions, label_horizon_sessions=holding, purge_sessions=purge)
    assert plan["status"] == "ready" and plan["required_sessions"] == required
    for prefix, length in (("warmup", 30), ("selection", 756), ("purge", expected_gap), ("holdout", 252)):
        assert len(sessions[sessions.slice_indexer(plan[f"{prefix}_start"], plan[f"{prefix}_end"])]) == length
    previous = plan_session_windows(sessions, as_of=sessions[-2].date().isoformat(),
                                    label_horizon_sessions=holding, purge_sessions=purge)
    assert previous["status"] == "insufficient_data" and previous["missing_sessions"] == 1
    assert "holdout_end" not in previous


@pytest.mark.asyncio
async def test_window_plan_http_requires_project_grant_and_has_no_write_side_effects(
    client, db_session, test_user, auth_headers, tmp_path, monkeypatch,
):
    monkeypatch.setenv("QUANTGPT_RESEARCH_SNAPSHOT_ROOT", str(tmp_path))
    project = await create_project(db_session, test_user.id, name="Registered panel")
    demo = await prepare_project_demo(db_session, test_user.id, project.id, snapshot_root=tmp_path)
    other = await create_project(db_session, test_user.id, name="No panel grant")
    await db_session.commit()
    template = {**demo["config"], "window": {"label_horizon_sessions": 5, "purge_sessions": 10, "warmup_observations": 90}}

    async def preview(project_id, config, **kwargs):
        return await client.post(f"/api/v1/research/projects/{project_id}/window-plan",
                                 headers=auth_headers, json={"config": config, **kwargs})

    response = await preview(project.id, template)
    assert response.status_code == 200
    plan = response.json()
    assert plan["status"] == "insufficient_data" and plan["round_setup"] is None
    assert (plan["required_sessions"], plan["available_sessions"], plan["missing_sessions"]) == (1108, 180, 928)
    early = await preview(project.id, template, as_of="2000-01-01")
    assert early.status_code == 200 and early.json()["available_sessions"] == 0
    denied = await preview(other.id, template)
    assert denied.status_code == 404
    forged = {**template, "data_inputs": [{**template["data_inputs"][0], "feed": "other"}]}
    assert (await preview(project.id, forged)).status_code == 422
    assert (await preview(project.id, demo["config"])).status_code == 422
    await db_session.refresh(project)
    assert "holdout" not in project.config
    for model in (ResearchRound, FinalWindowExposure, Experiment, Task):
        assert await db_session.scalar(select(func.count()).select_from(model)) == 0
