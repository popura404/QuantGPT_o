"""Iteration route promotion gate tests."""

import pytest

from quantgpt.task_store import REPORT_DIR, tasks
from quantgpt.validation.promotion import build_factor_validation_provenance, research_only_provenance

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _clean_tasks():
    tasks.clear()
    yield
    tasks.clear()


def _ready_provenance() -> dict:
    return build_factor_validation_provenance(
        oos_result={
            "direction_policy": "train_fixed",
            "train": {"metrics": {"long_short_sharpe": 1.0, "direction_adjusted_rank_ic_mean": 0.03}},
            "valid": {"metrics": {"long_short_sharpe": 0.9, "direction_adjusted_rank_ic_mean": 0.02}},
            "test": {"metrics": {"long_short_sharpe": 0.8, "direction_adjusted_rank_ic_mean": 0.02}},
        },
        oos_score={"decision": "candidate", "score": 80},
        data_quality={"enabled": True, "after_rows": 100, "after_stocks": 10},
        rolling_validation={"score": 70, "windows": [{"window_index": 0}]},
        placebo_test={"passed": True, "details": {"perm_pass": True, "decay_ok": True, "shift_ics": {"5": 0.01}}},
    )


async def test_select_candidate_rejects_research_only_candidate(client, test_user, auth_headers):
    tasks["iter-task"] = {
        "task_id": "iter-task",
        "user_id": str(test_user.id),
        "status": "iteration_completed",
        "candidates": [
            {
                "status": "success",
                "expression": "rank(close)",
                "validation_provenance": research_only_provenance(source="iteration_auto_full"),
            }
        ],
    }

    resp = await client.post(
        "/api/v1/tasks/iter-task/select_candidate",
        json={"candidate_index": 0},
        headers=auth_headers,
    )

    assert resp.status_code == 400
    assert resp.json()["detail"]["error_code"] == "CANDIDATE_PROMOTION_BLOCKED"


async def test_select_candidate_allows_promotion_ready_candidate(client, test_user, auth_headers):
    tasks["iter-task"] = {
        "task_id": "iter-task",
        "user_id": str(test_user.id),
        "status": "iteration_completed",
        "candidates": [
            {
                "status": "success",
                "expression": "rank(close)",
                "score": 80,
                "grade": "A",
                "report_url": "/api/v1/reports/report.html",
                "report_metrics": {},
                "backtest_summary": {},
                "validation_provenance": _ready_provenance(),
            }
        ],
    }

    resp = await client.post(
        "/api/v1/tasks/iter-task/select_candidate",
        json={"candidate_index": 0},
        headers=auth_headers,
    )

    assert resp.status_code == 200
    assert resp.json()["expression"] == "rank(close)"


async def test_get_iteration_task_returns_search_ledger_from_db(client, test_user, auth_headers, db_session):
    from quantgpt.models import Task as TaskModel

    db_session.add(
        TaskModel(
            id="iter-db-task",
            user_id=test_user.id,
            status="iteration_completed",
            task_type="iteration",
            expression="rank(close)",
            result={
                "parent_task_id": "parent-task",
                "candidates": [
                    {
                        "status": "success",
                        "expression": "rank(open)",
                        "score": 70,
                        "raw_score": 72,
                        "selection_score": 70,
                        "search_penalty": 2,
                    }
                ],
                "search_attempts": [
                    {
                        "id": "attempt-1",
                        "expression": "rank(open)",
                        "expression_key": "rank(open)",
                        "family_key": "rank(open)",
                        "generation_index": 0,
                        "from_mutation": True,
                        "from_crossover": False,
                        "failed": False,
                        "entered_next_round": True,
                        "search_penalty": 2,
                        "prior_expression_attempts": 1,
                        "prior_family_attempts": 1,
                    }
                ],
                "search_summary": {
                    "total_attempts": 1,
                    "failed_attempts": 0,
                    "mutation_attempts": 1,
                    "crossover_attempts": 0,
                    "advanced_attempts": 1,
                },
            },
        )
    )
    await db_session.commit()

    resp = await client.get("/api/v1/tasks/iter-db-task", headers=auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["search_summary"]["total_attempts"] == 1
    assert body["search_attempts"][0]["entered_next_round"] is True
    assert body["candidates"][0]["selection_score"] == 70


async def test_select_candidate_still_rejects_high_selection_score_research_only_candidate(client, test_user, auth_headers):
    tasks["iter-task"] = {
        "task_id": "iter-task",
        "user_id": str(test_user.id),
        "status": "iteration_completed",
        "candidates": [
            {
                "status": "success",
                "expression": "rank(close)",
                "score": 80,
                "selection_score": 99,
                "validation_provenance": research_only_provenance(source="iteration_auto_full"),
            }
        ],
    }

    resp = await client.post(
        "/api/v1/tasks/iter-task/select_candidate",
        json={"candidate_index": 0},
        headers=auth_headers,
    )

    assert resp.status_code == 400
    assert resp.json()["detail"]["error_code"] == "CANDIDATE_PROMOTION_BLOCKED"


async def test_report_ticket_allows_url_without_bearer_token(client, test_user, auth_headers):
    report_dir = REPORT_DIR / str(test_user.id)
    report_dir.mkdir(parents=True, exist_ok=True)
    report_file = report_dir / "backtest_report_ticket.html"
    report_file.write_text("<html>ticket report</html>", encoding="utf-8")

    ticket_resp = await client.post("/api/v1/reports/backtest_report_ticket.html/ticket", headers=auth_headers)
    assert ticket_resp.status_code == 200

    report_resp = await client.get(
        "/api/v1/reports/backtest_report_ticket.html",
        params={"ticket": ticket_resp.json()["ticket"]},
    )

    assert report_resp.status_code == 200
    assert "ticket report" in report_resp.text
    assert "script-src 'none'" in report_resp.headers["content-security-policy"]


async def test_report_ticket_is_single_use(client, test_user, auth_headers):
    report_dir = REPORT_DIR / str(test_user.id)
    report_dir.mkdir(parents=True, exist_ok=True)
    report_file = report_dir / "backtest_report_ticket_once.html"
    report_file.write_text("<html>ticket once</html>", encoding="utf-8")

    ticket_resp = await client.post("/api/v1/reports/backtest_report_ticket_once.html/ticket", headers=auth_headers)
    ticket = ticket_resp.json()["ticket"]
    first = await client.get("/api/v1/reports/backtest_report_ticket_once.html", params={"ticket": ticket})
    second = await client.get("/api/v1/reports/backtest_report_ticket_once.html", params={"ticket": ticket})

    assert first.status_code == 200
    assert second.status_code == 401
