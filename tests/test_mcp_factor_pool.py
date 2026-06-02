"""MCP factor-pool tool tests."""

import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from quantgpt import mcp_server
from quantgpt.factor_pool import MCP_SYSTEM_USER_ID
from quantgpt.models import Experiment, FactorPoolEntry, User


@pytest.fixture
def mcp_factor_pool_factory(monkeypatch, engine):
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(mcp_server, "_get_ledger_session_factory", lambda: factory)
    return factory


@pytest.mark.asyncio
async def test_mcp_save_list_update_facets_and_delete(mcp_factor_pool_factory):
    saved = json.loads(await mcp_server.save_factor_pool_entry(
        "rank(close)",
        name="Close rank",
        tags=["Quality"],
        category="Momentum",
        pool_status="accepted",
        universe="csi500",
        holding_period=10,
        metrics={"score": 82},
        validation_provenance={"promotion_state": "research_only"},
    ))
    entry = saved["entry"]
    assert saved["created"] is True
    assert entry["owner_user_id"] == str(MCP_SYSTEM_USER_ID)
    assert entry["pool_status"] == "accepted"
    assert entry["tags"] == ["category:momentum", "quality"]

    listed = json.loads(await mcp_server.list_factor_pool_entries(
        pool_status="accepted",
        category="momentum",
        tags=["quality"],
    ))
    assert listed["total"] == 1
    assert listed["entries"][0]["id"] == entry["id"]

    facets = json.loads(await mcp_server.list_factor_pool_tags())
    assert {"category_tag": "category:momentum", "category": "momentum", "count": 1} in facets["categories"]
    assert {"tag": "quality", "count": 1} in facets["tags"]

    updated = json.loads(await mcp_server.update_factor_pool_entry(
        entry["id"],
        pool_status="watchlist",
        tags=["retest"],
        category="Quality",
    ))
    assert updated["pool_status"] == "watchlist"
    assert updated["tags"] == ["category:quality", "retest"]

    detail = json.loads(await mcp_server.get_factor_pool_entry(entry["id"]))
    assert detail["id"] == entry["id"]
    assert detail["category_tag"] == "category:quality"

    deleted = json.loads(await mcp_server.delete_factor_pool_entry(entry["id"]))
    assert deleted == {"deleted": True, "entry_id": entry["id"]}

    missing = json.loads(await mcp_server.get_factor_pool_entry(entry["id"]))
    assert missing["error_code"] == "FACTOR_POOL_ERROR"


@pytest.mark.asyncio
async def test_mcp_accepted_status_does_not_promote_experiment(mcp_factor_pool_factory):
    result = json.loads(await mcp_server.save_factor_pool_entry(
        "rank(volume)",
        category="liquidity",
        pool_status="accepted",
        experiment_id="exp_manual",
    ))
    assert result["entry"]["pool_status"] == "accepted"

    async with mcp_factor_pool_factory() as session:
        system_user = (await session.execute(select(User).where(User.id == MCP_SYSTEM_USER_ID))).scalar_one()
        entries = (await session.execute(select(FactorPoolEntry))).scalars().all()
        experiments = (await session.execute(select(Experiment))).scalars().all()

    assert system_user.email == "mcp@system.internal"
    assert len(entries) == 1
    assert experiments == []
