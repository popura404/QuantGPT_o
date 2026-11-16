"""Coverage checks inspect session keys before a coordinator consumes a final window."""

import hashlib
import uuid

import pandas as pd
import pytest
from sqlalchemy import func, select

from quantgpt.models import FinalWindowExposure, ResearchAuditEvent, ResearchDirection, ResearchRound, Task
from quantgpt.research.contracts import EvaluationConfigV1
from quantgpt.research.demo import prepare_offline_demo
from quantgpt.research.projects import create_project
from quantgpt.research.round_final import _final_config, check_final_session_coverage, finalize_round


def test_snapshot_calendar_handles_nontrading_bounds_and_rejects_short_or_missing_windows(tmp_path):
    dates = pd.bdate_range("2024-01-02", periods=180)
    demo = prepare_offline_demo(tmp_path)
    first = next(d for d in dates[70:80] if d.weekday() == 0)
    last = next(d for d in dates[159:166] if d.weekday() == 4)
    holdout = {"start": (first - pd.Timedelta(days=2)).date().isoformat(),
               "end": (last + pd.Timedelta(days=2)).date().isoformat()}
    coverage = check_final_session_coverage(_final_config(demo["config"], holdout), tmp_path)
    assert coverage["first_scoring_session"] == first.date().isoformat()
    assert coverage["last_scoring_session"] == last.date().isoformat()
    with pytest.raises(ValueError, match="FINAL_WINDOW_INSUFFICIENT_SESSIONS"):
        check_final_session_coverage(_final_config(demo["config"], {**holdout, "end": first.date().isoformat()}), tmp_path)
    with pytest.raises(ValueError, match="FINAL_SNAPSHOT_SESSION_GAP"):
        check_final_session_coverage(_final_config(demo["config"], {**holdout, "end": "2025-01-10"}), tmp_path)

    selection = EvaluationConfigV1.model_validate(demo["config"])
    final = _final_config(demo["config"], holdout)
    both = check_final_session_coverage(final, tmp_path, selection_config=selection)
    assert both["selection_coverage"]["first_scoring_session"] == selection.window.start_session.isoformat()
    for start, end, reason in (("2023-03-01", "2023-04-01", "SELECTION_SNAPSHOT_SESSION_GAP"),
                               ("2024-01-05", "2024-03-05", "SELECTION_SNAPSHOT_WARMUP_INSUFFICIENT"),
                               ("2024-04-06", "2024-04-07", "SELECTION_WINDOW_INSUFFICIENT_SESSIONS")):
        invalid = EvaluationConfigV1.model_validate({**demo["config"], "window": {
            **demo["config"]["window"], "start_session": start, "end_session": end}})
        with pytest.raises(ValueError, match=reason):
            check_final_session_coverage(final, tmp_path, selection_config=invalid)


@pytest.mark.asyncio
async def test_missing_final_snapshot_sessions_leave_round_open_and_window_unexposed(db_session, test_user, tmp_path, monkeypatch):
    demo = prepare_offline_demo(tmp_path)
    monkeypatch.setenv("QUANTGPT_RESEARCH_SNAPSHOT_ROOT", str(tmp_path))
    project = await create_project(db_session, test_user.id, name="Incomplete final snapshot")
    secret = "key-for-coverage-test"
    row = ResearchRound(id=uuid.uuid4(), project_id=project.id, coordinator_id=test_user.id,
                        coordinator_digest=hashlib.sha256(secret.encode()).hexdigest(), selection_config=demo["config"],
                        holdout={"start": "2025-01-03", "end": "2025-05-01"}, status="open")
    db_session.add(row)
    await db_session.flush()
    db_session.add(ResearchDirection(round_id=row.id, name="A", hypothesis="H", actor_id=test_user.id,
                                     credential_digest="digest", finished=True))
    db_session.add(ResearchAuditEvent(project_id=project.id, actor_id=test_user.id, action="snapshot_registered",
        payload={"snapshot_id": demo["config"]["data_inputs"][0]["manifest_id"]}))
    await db_session.commit()
    with pytest.raises(ValueError, match="FINAL_SNAPSHOT_SESSION_GAP"):
        await finalize_round(db_session, test_user.id, project.id, round_id=row.id,
                             coordinator_key=secret, selection_run_id=uuid.uuid4())
    await db_session.refresh(row)
    assert row.status == "open" and row.frozen is None and row.final_evaluation_id is None
    assert await db_session.scalar(select(func.count()).select_from(FinalWindowExposure)) == 0
    assert await db_session.scalar(select(func.count()).select_from(Task)) == 0
