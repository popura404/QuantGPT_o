"""P07 service-level membership and cross-market identity regressions."""

import uuid

import pytest

from quantgpt.factor_pool import FactorPoolNotFoundError, get_factor_pool_entry, save_factor_pool_entry
from quantgpt.models import User
from quantgpt.research.projects import create_project, set_project_member


@pytest.mark.asyncio
async def test_project_members_share_entries_and_revocation_is_immediate(db_session, test_user):
    colleague = User(id=uuid.uuid4(), email="colleague@example.test", is_active=True)
    db_session.add(colleague)
    await db_session.flush()
    project = await create_project(db_session, test_user.id, name="共同因子研究")
    await set_project_member(db_session, test_user.id, project.id, colleague.id, "researcher")
    entry, _ = await save_factor_pool_entry(db_session, owner_user_id=colleague.id, project_id=project.id,
                                           data={"expression": "rank(close)", "market": "us_equity"})
    assert (await get_factor_pool_entry(db_session, owner_user_id=test_user.id, project_id=project.id,
                                       entry_id=entry.id)).id == entry.id
    await set_project_member(db_session, test_user.id, project.id, colleague.id, None)
    for context in (project.id, None):
        with pytest.raises(FactorPoolNotFoundError):
            await get_factor_pool_entry(db_session, owner_user_id=colleague.id, project_id=context, entry_id=entry.id)


@pytest.mark.asyncio
async def test_forged_project_cannot_read_or_write(db_session, test_user):
    other = User(id=uuid.uuid4(), email="other@example.test", is_active=True)
    db_session.add(other)
    await db_session.flush()
    project = await create_project(db_session, other.id, name="Private")
    with pytest.raises(FactorPoolNotFoundError):
        await save_factor_pool_entry(db_session, owner_user_id=test_user.id, project_id=project.id,
                                     data={"expression": "rank(close)"})


@pytest.mark.asyncio
async def test_same_expression_different_markets_does_not_overwrite(db_session, test_user):
    entries = []
    for market in ("us_equity", "a_share"):
        entry, created = await save_factor_pool_entry(db_session, owner_user_id=test_user.id,
                                                      data={"expression": "rank(close)", "market": market})
        assert created
        entries.append(entry)
    assert entries[0].id != entries[1].id


@pytest.mark.asyncio
async def test_mcp_server_resolved_actor_writes_visible_http_pool(client, auth_headers, test_user, monkeypatch, engine):
    import json

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from quantgpt import mcp_server
    from quantgpt.research.projects import MCP_ACTOR

    response = await client.post("/api/v1/research/projects", headers=auth_headers,
                                 json={"name": "MCP / Web", "market": "us_equity"})
    assert response.status_code == 201, response.text
    project = response.json()["id"]
    monkeypatch.setattr(mcp_server, "_get_ledger_session_factory", lambda: async_sessionmaker(engine, expire_on_commit=False))
    token = MCP_ACTOR.set(test_user.id)
    try:
        created = json.loads(await mcp_server.save_factor_pool_entry(expression="rank(close)", project_id=project,
                                                                     market="us_equity"))
    finally:
        MCP_ACTOR.reset(token)
    assert created["created"], created
    visible = await client.get(f"/api/v1/factor-pool?project_id={project}", headers=auth_headers)
    assert visible.status_code == 200
    assert visible.json()["entries"][0]["id"] == created["entry"]["id"]


@pytest.mark.asyncio
async def test_project_viewer_cannot_update(db_session, test_user):
    viewer = User(id=uuid.uuid4(), email="viewer@example.test", is_active=True)
    db_session.add(viewer)
    await db_session.flush()
    project = await create_project(db_session, test_user.id, name="Read only")
    await set_project_member(db_session, test_user.id, project.id, viewer.id, "viewer")
    with pytest.raises(FactorPoolNotFoundError):
        await save_factor_pool_entry(db_session, owner_user_id=viewer.id, project_id=project.id,
                                     data={"expression": "close"})
