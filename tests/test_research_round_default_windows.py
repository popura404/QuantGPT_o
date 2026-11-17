"""The MCP planner and round registration must use the same immutable windows."""

import json
import uuid

import pandas as pd
import pytest
from mcp.server.fastmcp import FastMCP
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from quantgpt.data_snapshots import freeze_market_frame
from quantgpt.models import ResearchRound, Task
from quantgpt.research import round_tools
from quantgpt.research.preparation import prepare_project_demo, register_project_snapshot
from quantgpt.research.projects import MCP_ACTOR, create_project
from quantgpt.research.rounds import create_round


@pytest.mark.asyncio
async def test_default_plan_and_round_share_frozen_windows_over_mcp(engine, db_session, test_user, tmp_path, monkeypatch):
    snapshots = tmp_path / "snapshots"
    shared = tmp_path / "imports"
    monkeypatch.setenv("QUANTGPT_RESEARCH_SNAPSHOT_ROOT", str(snapshots))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(round_tools, "_get_session_factory", lambda: factory)
    project = await create_project(db_session, test_user.id, name="Window planning")
    demo = await prepare_project_demo(db_session, test_user.id, project.id, snapshot_root=snapshots)
    template = {**demo["config"], "window": {"label_horizon_sessions": 5, "purge_sessions": 5}}
    directions = [{"name": "quality", "hypothesis": "Quality predicts future performance."}]
    days = pd.bdate_range("2019-01-02", periods=1100)
    frame = pd.DataFrame({"trade_date": days, "stock_code": "fixture", "open": 10.0, "high": 10.0,
                          "low": 10.0, "close": 10.0, "volume": 100})
    frame.attrs.update(calendar_sessions=[day.date().isoformat() for day in days], adjustment="raw", synthetic=True)
    manifest = freeze_market_frame(frame, shared, vendor="synthetic_offline_fixture", source_metadata={"synthetic": True})
    server = FastMCP("window-test")
    round_tools.register_round_tools(server)
    schemas = {tool.name: tool.inputSchema for tool in await server.list_tools()}
    assert set(schemas["plan_research_windows"]["required"]) == {"project_id", "config"}
    assert set(schemas["create_research_round"]["required"]) == {"project_id", "config", "directions"}

    async def call(name, **arguments):
        result = await server.call_tool(name, {"project_id": str(project.id), **arguments})
        content = result[0] if isinstance(result, tuple) else result
        return json.loads(content[0].text)

    token = MCP_ACTOR.set(test_user.id)
    try:
        insufficient = await call("plan_research_windows", config=template)
        assert insufficient["status"] == "insufficient_data" and insufficient["round_setup"] is None
        assert await call("create_research_round", config=template, directions=directions) == insufficient
        await db_session.refresh(project)
        assert "holdout" not in project.config
        assert await db_session.scalar(select(func.count()).select_from(ResearchRound)) == 0
        assert await db_session.scalar(select(func.count()).select_from(Task)) == 0

        snapshot = await register_project_snapshot(db_session, test_user.id, project.id, manifest["snapshot_id"],
                                                   snapshot_root=snapshots, import_root=shared)
        template = {**template, "data_inputs": [snapshot["data_input"]], "simulation_config": {
            **template["simulation_config"], "rebalance_anchor_session": days[0].date().isoformat()}}
        cutoff = days[-6].date().isoformat()
        plan = await call("plan_research_windows", config=template, as_of=cutoff)
        assert plan["status"] == "ready"
        assert plan["round_setup"]["holdout_end"] == cutoff
        assert await db_session.scalar(select(func.count()).select_from(ResearchRound)) == 0
        opened = await call("create_research_round", config=template, as_of=cutoff, directions=directions)
        assert opened["window_plan"] == plan
        row = await db_session.get(ResearchRound, uuid.UUID(opened["round_id"]))
        setup = plan["round_setup"]
        assert row.selection_config == setup["config"]
        assert (row.holdout["start"], row.holdout["end"]) == (setup["holdout_start"], setup["holdout_end"])
        retried = await call("create_research_round", **setup, directions=directions, coordinator_key=opened["coordinator_key"])
        assert retried["round_id"] == opened["round_id"] and retried["directions"] == opened["directions"]
        assert "window_plan" not in retried
        (snapshots / manifest["snapshot_id"] / "market.parquet").rename(tmp_path / "moved.parquet")
        missing = await call("plan_research_windows", config=template)
        assert missing["error_code"] == "RESEARCH_ROUND_BLOCKED" and str(tmp_path) not in missing["message"]
        legacy_missing = await call("create_research_round", **setup, directions=directions, coordinator_key=opened["coordinator_key"])
        assert legacy_missing == {"error_code": "RESEARCH_ROUND_BLOCKED", "message": "RESEARCH_INPUT_FILE_UNAVAILABLE"}
    finally:
        MCP_ACTOR.reset(token)


@pytest.mark.asyncio
async def test_round_explicit_short_windows_and_mixed_requests(db_session, test_user, tmp_path, monkeypatch):
    monkeypatch.setenv("QUANTGPT_RESEARCH_SNAPSHOT_ROOT", str(tmp_path))
    project = await create_project(db_session, test_user.id, name="Explicit dates")
    demo = await prepare_project_demo(db_session, test_user.id, project.id, snapshot_root=tmp_path)
    setup = demo["round_setup"]
    directions = [{"name": "value", "hypothesis": "Lower valuations predict returns."}]
    config = setup["config"]
    partial_selection = {**config, "window": {k: v for k, v in config["window"].items() if k != "end_session"}}
    for invalid in ({"config": config}, {"config": config, "holdout_start": setup["holdout_start"]},
                    {**setup, "config": partial_selection}):
        with pytest.raises(ValueError, match="ROUND_WINDOW_DATES_INCOMPLETE"):
            await create_round(db_session, test_user.id, project.id, directions=directions, **invalid)
    with pytest.raises(ValueError, match="AS_OF_WITH_EXPLICIT_WINDOWS"):
        await create_round(db_session, test_user.id, project.id, directions=directions, as_of=setup["holdout_end"], **setup)
    assert "holdout" not in project.config
    opened = await create_round(db_session, test_user.id, project.id, directions=directions, **setup)
    row = await db_session.get(ResearchRound, uuid.UUID(opened["round_id"]))
    historical = {**row.selection_config, "engine": {**row.selection_config["engine"], "code_version": "sha256:" + "a" * 64}}
    row.selection_config = historical
    await db_session.flush()
    retry = await create_round(db_session, test_user.id, project.id, directions=directions, **setup,
                               coordinator_key=opened["coordinator_key"])
    assert retry == opened and "window_plan" not in opened and row.selection_config == historical
    mismatch = {**config, "window": {**config["window"], "warmup_observations": 15}}
    with pytest.raises(ValueError, match="ROUND_ALREADY_CONFIGURED"):
        await create_round(db_session, test_user.id, project.id, directions=directions, **{**setup, "config": mismatch},
                           coordinator_key=opened["coordinator_key"])
