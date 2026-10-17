"""Bounded task summaries, aggregation, legacy merging, and visibility regressions."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, event
from sqlalchemy.sql import Select

from quantgpt.auth import create_api_key_for_user
from quantgpt.models import ProjectMember, ResearchProject, User
from quantgpt.models import Task as TaskModel
from quantgpt.task_store import TERMINAL_TASK_STATUSES, tasks

pytestmark = pytest.mark.asyncio
_BASE_TIME = datetime(2025, 1, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _clear_task_cache():
    tasks.clear()
    yield
    tasks.clear()


@pytest.fixture
def capture_selects(engine):
    statements = []

    def capture(_connection, clause, _multiparams, _params, _options):
        if isinstance(clause, Select):
            statements.append(clause)

    event.listen(engine.sync_engine, "before_execute", capture)
    try:
        yield statements
    finally:
        event.remove(engine.sync_engine, "before_execute", capture)


def _assert_no_full_task_results(statements):
    # Includes authentication SQL, which previously eagerly loaded User.tasks
    # and User.sessions -> Session.tasks before the route even ran.
    for statement in statements:
        for column in statement.selected_columns:
            assert not (
                getattr(column, "name", None) == "result"
                and getattr(getattr(column, "table", None), "name", None) == "tasks"
            ), str(statement)


def _paged_task_selects(statements):
    return [statement for statement in statements
            if statement._limit_clause is not None and "task_id" in statement.selected_columns]


def _record(task_id, user_id, second, *, status="completed", rating="A", session_id=None, task_type="backtest"):
    return TaskModel(
        id=task_id, user_id=user_id, status=status, task_type=task_type,
        session_id=session_id, created_at=_BASE_TIME + timedelta(seconds=second),
        updated_at=_BASE_TIME + timedelta(seconds=second + 1),
        result={"interpretation": {"rating": rating}},
    )


@pytest.mark.parametrize("auth_mode", ["jwt", "api_key"])
async def test_pagination_and_stats_do_not_load_full_results(
    client, test_user, auth_headers, db_session, engine, capture_selects, auth_mode,
):
    blob = "large-artifact-" * 10_000
    for index in range(12):
        record = _record(f"db-{index:03}", test_user.id, index)
        record.params = {"prompt": "research prompt", "tag": "quality"}
        record.result = {
            "params": {"expression": "rank(close)", "universe": "hs300"},
            "metrics": {"sharpe": 1.23, "cagr": 0.15, "max_drawdown": -0.2},
            "scoring": {"score": 72.5, "grade": "B", "decision": "research"},
            "interpretation": {"rating": "A" if index % 2 == 0 else ""},
            "backtest_summary": {"wq_rating": "B", "ic_ir": 0.5},
            "stock_factor_data": {"payload": blob},
            "nav_series": [{"date": "2025-01-01", "value": 1.0}] * 1_000,
        }
        db_session.add(record)
    await db_session.commit()
    if auth_mode == "api_key":
        token = await create_api_key_for_user(test_user.id, "list-test", db_session)
        auth_headers = {"Authorization": f"Bearer {token}"}
    capture_selects.clear()

    response = await client.get("/api/v1/tasks", params={"page": 3, "page_size": 3}, headers=auth_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 12
    assert [task["task_id"] for task in body["tasks"]] == ["db-005", "db-004", "db-003"]
    task = body["tasks"][0]
    assert task["summary_only"] is True
    assert task["params"] == {"prompt": "research prompt", "tag": "quality"}
    assert task["result"]["metrics"]["sharpe"] == 1.23
    assert task["result"]["scoring"]["score"] == 72.5
    assert task["result"]["params"]["expression"] == "rank(close)"
    assert "stock_factor_data" not in task["result"]
    assert "nav_series" not in task["result"]
    assert len(response.content) < 10_000
    statements = _paged_task_selects(capture_selects)
    assert len(statements) == 1
    assert statements[0]._limit_clause.value == 3
    assert statements[0]._offset_clause.value == 6

    stats = await client.get("/api/v1/tasks/stats", headers=auth_headers)
    assert stats.status_code == 200, stats.text
    assert stats.json() == {
        "total": 12, "completed": 12, "failed": 0, "running": 0,
        "success_rate": 100.0, "rating_distribution": {"A": 6, "B": 6},
    }
    assert any(statement._group_by_clauses for statement in capture_selects)
    _assert_no_full_task_results(capture_selects)

    detail = await client.get("/api/v1/tasks/db-005", headers=auth_headers)
    assert detail.status_code == 200
    assert not detail.json().get("summary_only")
    assert detail.json()["result"]["stock_factor_data"]["payload"] == blob


async def test_legacy_memory_merge_has_stable_page_boundaries_and_bounded_db_window(
    client, test_user, auth_headers, db_session, capture_selects,
):
    expected = []
    for index in range(12):
        record = _record(f"db-{index:03}", test_user.id, index * 2)
        db_session.add(record)
        expected.append((index * 2, record.id))
    for index in range(4):
        task_id = f"legacy-{index}"
        second = index * 7
        tasks[task_id] = {
            "task_id": task_id, "user_id": str(test_user.id), "status": "completed",
            "task_type": "backtest", "created_at": (_BASE_TIME + timedelta(seconds=second)).isoformat(),
            "result": {"interpretation": {"rating": "B"}, "stock_factor_data": {"large": [0] * 10_000}},
        }
        expected.append((second, task_id))
    # The cache must not duplicate a stored row or override its status/rating.
    tasks["db-011"] = {"task_id": "db-011", "user_id": str(test_user.id), "status": "failed",
                       "created_at": (_BASE_TIME + timedelta(days=1)).timestamp(),
                       "result": {"interpretation": {"rating": "D"}}}
    await db_session.commit()
    expected_ids = [task_id for _second, task_id in sorted(expected, reverse=True)]
    seen = []
    capture_selects.clear()
    for page in range(1, 7):
        response = await client.get("/api/v1/tasks", params={"page": page, "page_size": 3}, headers=auth_headers)
        assert response.status_code == 200, response.text
        assert response.json()["total"] == 16
        seen.extend(task["task_id"] for task in response.json()["tasks"])
    assert seen == expected_ids
    assert all(statement._limit_clause.value <= 7 for statement in _paged_task_selects(capture_selects))
    deep = await client.get("/api/v1/tasks?page=100&page_size=3", headers=auth_headers)
    assert deep.json()["tasks"] == []
    assert deep.json()["total"] == 16
    failed = await client.get("/api/v1/tasks?status=failed", headers=auth_headers)
    assert failed.json()["total"] == 0
    assert failed.json()["tasks"] == []
    _assert_no_full_task_results(capture_selects)
    stats = (await client.get("/api/v1/tasks/stats", headers=auth_headers)).json()
    assert stats["total"] == 16
    assert stats["completed"] == 16
    assert stats["failed"] == 0
    assert stats["rating_distribution"] == {"A": 12, "B": 4}


@pytest.mark.parametrize("query", [
    {"status": "running"}, {"status": "completed"}, {"task_type": "composite"},
    {"rating": "B"}, {"rating": ""}, {"session_id": "first", "task_type": "composite", "rating": "B"},
])
async def test_filters_match_persisted_and_legacy_tasks(client, test_user, auth_headers, db_session, query):
    first_session, second_session = uuid.uuid4(), uuid.uuid4()
    states = [
        ("pending", "backtest", "A", first_session),
        ("backtesting", "composite", "B", first_session),
        ("completed", "composite", "B", first_session),
        ("cancelled", "backtest", "A", second_session),
        ("failed", "backtest", "", second_session),
    ]
    for index, (status, task_type, rating, session_id) in enumerate(states):
        record = _record(f"db-{index}", test_user.id, index, status=status, task_type=task_type,
                         rating=rating, session_id=session_id)
        # The interpretation fallback must behave identically in SQL and Python.
        record.result = {"interpretation": {"rating": ""}, "backtest_summary": {"wq_rating": rating}}
        db_session.add(record)
        tasks[f"legacy-{index}"] = {
            "task_id": f"legacy-{index}", "user_id": str(test_user.id), "status": status,
            "task_type": task_type, "session_id": str(session_id), "created_at": _BASE_TIME.timestamp() + index,
            "result": record.result,
        }
    await db_session.commit()
    query = dict(query)
    if query.get("session_id") == "first":
        query["session_id"] = str(first_session).upper()
    expected = set()
    for index, (status, task_type, rating, session_id) in enumerate(states):
        if "status" in query:
            if query["status"] == "running" and status in TERMINAL_TASK_STATUSES:
                continue
            if query["status"] != "running" and status != query["status"]:
                continue
        if "task_type" in query and query["task_type"] != task_type:
            continue
        if "rating" in query and query["rating"] != rating:
            continue
        if "session_id" in query and uuid.UUID(query["session_id"]) != session_id:
            continue
        expected.update({f"db-{index}", f"legacy-{index}"})
    response = await client.get("/api/v1/tasks", params=query, headers=auth_headers)
    assert response.status_code == 200, response.text
    assert {task["task_id"] for task in response.json()["tasks"]} == expected
    assert response.json()["total"] == len(expected)


async def test_project_authorization_applies_to_totals_pages_cache_and_stats(
    client, test_user, auth_headers, db_session,
):
    other = User(id=uuid.uuid4(), email="other-listing@example.test")
    db_session.add(other)
    shared = ResearchProject(id=uuid.uuid4(), owner_user_id=other.id, name="shared")
    private = ResearchProject(id=uuid.uuid4(), owner_user_id=other.id, name="private")
    db_session.add_all([shared, private, ProjectMember(project_id=shared.id, user_id=test_user.id, role="viewer")])
    await db_session.flush()
    own = _record("own", test_user.id, 1)
    visible_shared = _record("shared", other.id, 2)
    visible_shared.project_id = shared.id
    hidden = _record("private", test_user.id, 3)
    hidden.project_id = private.id
    db_session.add_all([own, visible_shared, hidden, _record("other", other.id, 4)])
    # A stale cache must not bypass a persisted task's current project scope.
    tasks["private"] = {"task_id": "private", "user_id": str(test_user.id), "status": "completed"}
    tasks["legacy"] = {
        "task_id": "legacy", "user_id": str(other.id), "project_id": str(shared.id),
        "status": "completed", "created_at": _BASE_TIME.timestamp() + 5,
        "result": {"interpretation": {"rating": "B"}},
    }
    await db_session.commit()
    response = await client.get("/api/v1/tasks?page_size=2", headers=auth_headers)
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 3
    assert [task["task_id"] for task in response.json()["tasks"]] == ["legacy", "shared"]
    stats = (await client.get("/api/v1/tasks/stats", headers=auth_headers)).json()
    assert stats["total"] == 3
    assert stats["rating_distribution"] == {"A": 2, "B": 1}

    await db_session.execute(delete(ProjectMember).where(ProjectMember.user_id == test_user.id))
    await db_session.commit()
    response = await client.get("/api/v1/tasks", headers=auth_headers)
    assert response.json()["total"] == 1
    assert [task["task_id"] for task in response.json()["tasks"]] == ["own"]
    assert (await client.get("/api/v1/tasks/stats", headers=auth_headers)).json()["total"] == 1


async def test_invalid_session_filter_is_rejected(client, test_user, auth_headers, db_session):
    db_session.add(_record("private-filter", test_user.id, 1))
    await db_session.commit()
    response = await client.get("/api/v1/tasks?session_id=invalid", headers=auth_headers)
    assert response.status_code == 422


async def test_unpublished_terminal_cache_does_not_appear_in_list_or_stats(client, test_user, auth_headers):
    tasks["unpublished"] = {
        "task_id": "unpublished", "user_id": str(test_user.id), "status": "completed",
        "attempt_id": "attempt", "revision": 5, "_persisted_revision": 4,
    }
    response = await client.get("/api/v1/tasks", headers=auth_headers)
    assert response.json()["tasks"] == []
    assert response.json()["total"] == 0
    assert (await client.get("/api/v1/tasks/stats", headers=auth_headers)).json()["total"] == 0


async def test_legacy_persistence_during_listing_does_not_duplicate_rows(
    client, test_user, auth_headers, monkeypatch,
):
    from quantgpt.routes import backtest_tasks

    tasks["racing"] = {
        "task_id": "racing", "user_id": str(test_user.id), "status": "completed",
        "created_at": _BASE_TIME.timestamp(), "result": {"interpretation": {"rating": "A"}},
    }
    original = backtest_tasks._legacy_task_summaries

    async def persist_after_lookup(db, user, project_ids):
        legacy = await original(db, user, project_ids)
        db.add(_record("racing", test_user.id, 0))
        await db.commit()
        return legacy

    monkeypatch.setattr(backtest_tasks, "_legacy_task_summaries", persist_after_lookup)
    response = await client.get("/api/v1/tasks", headers=auth_headers)
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 1
    assert [task["task_id"] for task in response.json()["tasks"]] == ["racing"]


async def test_session_directory_does_not_eagerly_load_task_results(
    client, test_user, auth_headers, db_session, capture_selects,
):
    from quantgpt.models import Session

    session_id = uuid.uuid4()
    db_session.add(Session(id=session_id, user_id=test_user.id, name="Research session", market="a_share"))
    task = _record("session-task", test_user.id, 0, session_id=session_id)
    task.result = {"stock_factor_data": {"large": "artifact" * 20_000}}
    db_session.add(task)
    await db_session.commit()
    capture_selects.clear()
    response = await client.get("/api/v1/sessions", headers=auth_headers)
    assert response.status_code == 200, response.text
    assert response.json()["sessions"][0]["id"] == str(session_id)
    assert response.json()["sessions"][0]["name"] == "Research session"
    assert len(response.content) < 1_000
    _assert_no_full_task_results(capture_selects)
    assert not any(getattr(table, "name", None) == "tasks"
                   for statement in capture_selects for table in statement.get_final_froms())
