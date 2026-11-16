import json
import uuid

import pandas as pd
import pytest

from quantgpt.data_snapshots import freeze_market_frame
from quantgpt.research.contracts import DataInputRef
from quantgpt.research.evaluations import authorize_data_inputs
from quantgpt.research.hypotheses import register_hypothesis
from quantgpt.research.preparation import get_project_preparation, list_project_imports, register_project_snapshot
from quantgpt.research.projects import ProjectAccessError, create_project
from tests.test_research_evaluations import config, definition


@pytest.mark.asyncio
async def test_grant_verified_panel_and_discover_project_inputs(db_session, test_user, tmp_path, monkeypatch):
    project = await create_project(db_session, test_user.id, name="Candidate research")
    other = await create_project(db_session, test_user.id, name="Unrelated project")
    await db_session.commit()
    frame = pd.DataFrame([{"stock_code": "A", "trade_date": pd.Timestamp("2024-02-01"), "open": 9.8,
                           "high": 10.3, "low": 9.7, "close": 10.0, "volume": 1000.0}])
    frame.attrs["adjustment"] = "raw"
    public, private = tmp_path / "catalog", tmp_path / "snapshots"
    manifest = freeze_market_frame(frame, public, vendor="fixture_vendor")
    identity = manifest["snapshot_id"]
    another = freeze_market_frame(frame, public, vendor="another_fixture_vendor")["snapshot_id"]
    confidential = freeze_market_frame(frame, private, vendor="private_vendor")["snapshot_id"]
    monkeypatch.setenv("QUANTGPT_RESEARCH_IMPORT_ROOT", str(public))
    page = await list_project_imports(db_session, test_user.id, project.id, snapshot_root=private, limit=1)
    next_page = await list_project_imports(db_session, test_user.id, project.id, snapshot_root=private, after=page["next_cursor"])
    ids = {row["manifest_id"] for row in page["snapshots"] + next_page["snapshots"]}
    assert ids == {identity, another} and confidential not in ids and next_page["next_cursor"] is None
    with pytest.raises(ProjectAccessError):
        await register_project_snapshot(db_session, uuid.uuid4(), project.id, identity, snapshot_root=tmp_path)
    with pytest.raises(ValueError, match="INVALID_SNAPSHOT_ID"):
        await register_project_snapshot(db_session, test_user.id, project.id, "../manifest.json", snapshot_root=tmp_path)
    registered = await register_project_snapshot(db_session, test_user.id, project.id, identity, snapshot_root=private, import_root=public)
    again = await register_project_snapshot(db_session, test_user.id, project.id, identity, snapshot_root=private)
    assert again == registered
    assert registered["data_input"]["content_sha256"] == manifest["content_hash"].split(":")[1]
    assert registered["external_data_acceptance"] == "not_run"
    ref = DataInputRef.model_validate(registered["data_input"])
    await authorize_data_inputs(db_session, project.id, config().model_copy(update={"data_inputs": (ref,)}))
    for override in ({"source": "other_vendor"}, {"feed": "other_feed"}, {"adjustment": "split_adjusted"}):
        with pytest.raises(ValueError, match="REGISTERED_DATA_INPUT_MISMATCH"):
            await authorize_data_inputs(db_session, project.id, config().model_copy(update={"data_inputs": (ref.model_copy(update=override),)}))
    await register_hypothesis(db_session, test_user.id, project.id, definition(), economic_hypothesis="Momentum")
    await db_session.commit()
    state = await get_project_preparation(db_session, test_user.id, project.id, snapshot_root=private)
    assert state["snapshots"] == [registered] and state["hypotheses"][0]["economic_hypothesis"] == "Momentum"
    assert (await get_project_preparation(db_session, test_user.id, other.id, snapshot_root=private))["snapshots"] == []
    with pytest.raises(ProjectAccessError):
        await register_project_snapshot(db_session, test_user.id, other.id, identity, snapshot_root=private, import_root=tmp_path / "empty")
    (private / identity / "market.parquet").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="snapshot file content hash mismatch"):
        await register_project_snapshot(db_session, test_user.id, project.id, identity, snapshot_root=private)
    state = await get_project_preparation(db_session, test_user.id, project.id, snapshot_root=private)
    assert state["snapshots"][0]["snapshot_integrity"] == "blocked"


@pytest.mark.asyncio
async def test_mcp_preparation_and_http_share_project_scope(engine, test_user, tmp_path, monkeypatch, client, auth_headers):
    from mcp.server.fastmcp import FastMCP
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from quantgpt.research import preparation_tools, round_tools
    from quantgpt.research.projects import MCP_ACTOR

    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(preparation_tools, "_get_session_factory", lambda: factory)
    monkeypatch.setattr(round_tools, "_get_session_factory", lambda: factory)
    monkeypatch.setenv("QUANTGPT_RESEARCH_SNAPSHOT_ROOT", str(tmp_path / "private"))
    monkeypatch.setenv("QUANTGPT_RESEARCH_IMPORT_ROOT", str(tmp_path / "shared"))
    server = FastMCP("preparation-test")
    preparation_tools.register_preparation_tools(server)
    round_tools.register_round_tools(server)

    async def call(tool_name, **params):
        result = await server.call_tool(tool_name, params)
        content = result[0] if isinstance(result, tuple) else result
        return json.loads(content[0].text)

    token = MCP_ACTOR.set(test_user.id)
    try:
        project = await call("create_research_project", name="MCP research", market="demo_global_equity")
        key = {"project_id": project["id"]}
        demo = await call("prepare_research_demo", **key)
        assert demo["synthetic"]
        demo["config"]["window"]["end_session"] = "2024-05-31"
        invalid = await call("create_research_round", **key, config=demo["config"], holdout_start="2025-01-02",
                             holdout_end="2025-04-01", directions=[{"name": "trend", "hypothesis": "Momentum"}])
        assert "error_code" in invalid
        assert (await call("get_research_preparation", **key))["holdout"] is None
        unsupported = {**demo["config"], "window": {**demo["config"]["window"], "label_horizon_sessions": 1}}
        invalid = await call("create_research_round", **key, config=unsupported, holdout_start="2024-06-03",
                             holdout_end="2024-09-02", directions=[{"name": "trend", "hypothesis": "Momentum"}])
        assert "LABEL_HORIZON_UNSUPPORTED" in invalid.get("message", ""), invalid
        assert (await call("get_research_preparation", **key))["holdout"] is None
        missing_selection = {**demo["config"], "window": {
            **demo["config"]["window"], "start_session": "2023-09-01"}}
        invalid = await call("create_research_round", **key, config=missing_selection, holdout_start="2024-06-03",
                             holdout_end="2024-09-02", directions=[{"name": "trend", "hypothesis": "Momentum"}])
        assert "SELECTION_SNAPSHOT_SESSION_GAP" in invalid.get("message", ""), invalid
        assert (await call("get_research_preparation", **key))["holdout"] is None
        holdout = await call("register_research_holdout", **key, start="2024-06-03", end="2024-09-02")
        assert holdout["start"] == "2024-06-03"
        await call("register_research_hypothesis", **key, definition=demo["definitions"][0], economic_hypothesis="Momentum")
        state = await call("get_research_preparation", **key)
        assert state["snapshots"][0]["declared_synthetic"] and state["hypotheses"][0]["economic_hypothesis"] == "Momentum"
        assert state["snapshots"][0]["data_input"] == demo["config"]["data_inputs"][0]
        other = await call("create_research_project", name="Another project")
        manifest_id = state["snapshots"][0]["manifest_id"]
        denied = await call("register_research_snapshot", project_id=other["id"], manifest_id=manifest_id)
        assert denied["error_code"] == "PROJECT_OR_RESOURCE_UNAVAILABLE"
        path = f"/api/v1/research/projects/{other['id']}/snapshots/{manifest_id}"
        assert (await client.post(path, headers=auth_headers)).status_code == 404
        state_path = f"/api/v1/research/projects/{project['id']}/preparation"
        assert (await client.get(state_path, headers=auth_headers)).json() == state
        managed = await call("create_research_round", **key, config=demo["config"], holdout_start=holdout["start"],
                             holdout_end=holdout["end"], directions=[{"name": "momentum", "hypothesis": "Momentum"}])
        assert "round_id" in managed, managed
        bypass = await client.post(f"/api/v1/research/projects/{project['id']}/evaluations", headers=auth_headers,
                                   json={"config": demo["config"], "definitions": demo["definitions"]})
        assert bypass.status_code == 422 and "MANAGED_ROUND_TASK_REQUIRED" in bypass.text, bypass.text
        outsider = MCP_ACTOR.set(uuid.uuid4())
        try:
            assert (await call("get_research_preparation", **key))["error_code"] == "PROJECT_OR_RESOURCE_UNAVAILABLE"
        finally:
            MCP_ACTOR.reset(outsider)
    finally:
        MCP_ACTOR.reset(token)
