"""Four direction credentials coordinate through one real MCP stdio connection."""

import asyncio
import json
import shutil
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pandas as pd
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from sqlalchemy import create_engine

from quantgpt.models import Task
from quantgpt.research.projects import MCP_SYSTEM_ACTOR


async def call(client, tool_name, **arguments):
    reply = await client.call_tool(tool_name, arguments)
    assert not reply.isError, reply.content
    return json.loads(reply.content[0].text)


async def completed(client, submitted):
    assert "task_id" in submitted, submitted
    async with asyncio.timeout(120):
        while True:
            result = await call(client, "get_mcp_task_status", task_id=submitted["task_id"], include_result=True)
            if result["status"] == "completed":
                return result["result"]
            assert result["status"] in {"queued", "running"}, result
            await asyncio.sleep(.1)


def select_candidates(rows):
    return sorted(rows, key=lambda expr: (-rows[expr]["summary"]["metrics"]["direction_adjusted_rank_ic_mean"],
                                          rows[expr]["evaluation_hash"]))[:2]


@pytest.mark.asyncio
async def test_four_agents_submit_merge_and_finalize_over_one_stdio_connection(tmp_path):
    checkout = tmp_path / "server"
    repository = Path(__file__).resolve().parents[1]
    shutil.copytree(repository / "quantgpt", checkout / "quantgpt", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(repository / "requirements", checkout / "requirements")
    url = f"sqlite+aiosqlite:///{tmp_path / 'round.sqlite'}"
    root = tmp_path / "snapshots"
    days = pd.bdate_range("2024-01-02", periods=180)
    env = {"DATABASE_URL": url, "QUANTGPT_RESEARCH_SNAPSHOT_ROOT": str(root),
           "QUANTGPT_CACHE_ONLY": "true", "AUTH_DISABLED": "false",
           "JWT_SECRET_KEY": "test-secret-key-for-ci-only-do-not-use-in-production", "QUANTGPT_TASK_BACKEND": "thread"}
    server = StdioServerParameters(command=sys.executable, args=["-B", "-m", "quantgpt", "--transport", "stdio"],
                                   cwd=checkout, env=env)
    async with stdio_client(server) as streams, ClientSession(*streams) as client:
        await client.initialize()
        project = await call(client, "create_research_project", name="One shared MCP principal", market="demo_global_equity")
        project_id = project["id"]
        demo = await call(client, "prepare_research_demo", project_id=project_id)
        assert demo["synthetic"]
        config = demo["config"]
        config["window"]["end_session"] = days[94].date().isoformat()
        names = ("trend", "reversal", "volatility", "price_level")
        created = await call(client, "create_research_round", project_id=project_id, config=config,
            holdout_start=days[100].date().isoformat(), holdout_end=days[-1].date().isoformat(),
            directions=[{"name": name, "hypothesis": f"Preregistered {name} hypothesis"} for name in names])
        assert "round_id" in created, created
        scope = {"project_id": project_id, "round_id": created["round_id"]}
        coordinator = {**scope, "coordinator_key": created["coordinator_key"]}
        credentials = [{**scope, "direction_id": item["direction_id"], "direction_key": item["direction_key"]}
                       for item in created["directions"]]
        assert len({cred["direction_key"] for cred in credentials}) == 4
        # Historical legacy ledger fixture only. Every import, proposal and
        # numerical evaluation below goes through the stdio protocol.
        engine = create_engine(f"sqlite:///{tmp_path / 'round.sqlite'}")
        with engine.begin() as conn:
            conn.execute(Task.__table__.insert().values(id="legacy-task", user_id=MCP_SYSTEM_ACTOR,
                task_type="iteration", status="iteration_completed", result={"selected_candidate_index": 0,
                "search_attempts": [{"id": "win", "expression": "rank(close)", "status": "success", "raw_score": 83},
                                    {"id": "loss", "expression": "-rank(open)", "status": "success", "raw_score": 0},
                                    {"id": "bad", "expression": "rank(", "status": "failed", "raw_score": 0}],
                "candidates": [{"status": "success", "expression": "rank(close)", "score": 83,
                                "search_attempt": {"id": "win"}}]}))
        engine.dispose()
        for cred in credentials[:2]:
            imported = await call(client, "import_research_iteration", **cred, task_id="legacy-task")
            assert imported["observed_legacy_trials"] == 2, imported
        # Same project, direction credentials, immutable inputs and historical
        # trials in both deployments; only the order of publication changes.
        with (
            closing(sqlite3.connect(tmp_path / "round.sqlite")) as source,
            closing(sqlite3.connect(tmp_path / "reverse.sqlite")) as backup,
        ):
            source.backup(backup)
        raw_final = {**config, "window": {**config["window"], "phase": "final",
                     "start_session": days[100].date().isoformat(), "end_session": days[-1].date().isoformat()}}
        bypass = await call(client, "evaluate_factors", project_id=project_id, definitions=demo["definitions"],
                            config=raw_final, submit_only=False)
        assert "MANAGED_ROUND_TASK_REQUIRED" in bypass.get("message", ""), bypass
        expressions = [["rank(close)", "rank(ts_mean(close, 5))"], ["rank(close)", "-rank(close)"],
                       ["rank(ts_delta(close, 3))"], ["rank(close / ts_mean(close, 10))"]]
        requests = [{**cred, "definitions": [{**demo["definitions"][0], "expression": expr} for expr in batch],
                     "idempotency_key": f"propose-{i}"} for i, (cred, batch) in enumerate(zip(credentials, expressions))]
        wrong_branch = await call(client, "submit_research_direction",
                                  **{**requests[0], "direction_key": credentials[1]["direction_key"]})
        assert "error_code" in wrong_branch and "task_id" not in wrong_branch
        submissions = await asyncio.gather(*(call(client, "submit_research_direction", **req) for req in requests))
        repeated = await call(client, "submit_research_direction", **requests[0])
        assert repeated["task_id"] == submissions[0]["task_id"]
        completion_order = []

        async def observe(index, submitted):
            result = await completed(client, submitted)
            completion_order.append(index)
            return result

        results = await asyncio.gather(*(observe(i, submitted) for i, submitted in enumerate(submissions)))
        results_by_expr = {expr: row for batch, result in zip(expressions, results)
                           for expr, row in zip(batch, result["evaluations"], strict=True)}
        assert all(row["summary"]["performance_observed"] for row in results_by_expr.values()), results
        assert results[0]["evaluations"][0]["evaluation_id"] == results[1]["evaluations"][0]["evaluation_id"]
        assert sum(result["batch_diagnostics"]["engine_calls"] for result in results) == len(results_by_expr) == 5
        await asyncio.gather(*(call(client, "finish_research_direction", **cred) for cred in credentials[:-1]))
        merged = await call(client, "get_research_round", **scope)
        visible = json.dumps(merged)
        assert coordinator["coordinator_key"] not in visible
        assert all(cred["direction_key"] not in visible for cred in credentials)
        by_direction = {item["direction_id"]: item for item in merged["directions"]}
        for cred, submission, result in zip(credentials, submissions, results, strict=True):
            direction = by_direction[cred["direction_id"]]
            assert submission["task_id"] in {t["task_id"] for t in direction["tasks"]}
            assert {r["evaluation_id"] for r in result["evaluations"]} == set(direction["evaluation_ids"])

        selected = select_candidates(results_by_expr)
        spec = demo["strategy_template"]
        spec["factors"] = [{"id": f"f{i}", "expression": expr, "direction": config["direction"], "weight": .5}
                           for i, expr in enumerate(selected)]
        spec["factor_evaluations"] = [
            {**{k: results_by_expr[expr][k] for k in
                ("evaluation_id", "evaluation_hash", "definition_hash", "project_id", "backend")}, "scope": "selection"}
            for expr in selected]
        strategy = await completed(client, await call(client, "submit_round_strategy", **coordinator, spec=spec))
        selection_id = strategy["strategy_run_id"]
        premature = await call(client, "finalize_research_round", **coordinator, selection_run_id=selection_id)
        assert "ROUND_DIRECTIONS_UNFINISHED" in premature.get("message", ""), premature
        assert (await call(client, "finish_research_direction", **credentials[-1]))["finished"]
        forbidden = await call(client, "finalize_research_round", **scope,
                               coordinator_key=credentials[0]["direction_key"], selection_run_id=selection_id)
        assert "task_id" not in forbidden, forbidden
        accepted = await call(client, "finalize_research_round", **coordinator, selection_run_id=selection_id)
        final = await completed(client, accepted)
        retry = await call(client, "finalize_research_round", **coordinator, selection_run_id=selection_id)
        assert retry["task_id"] == accepted["task_id"]
        assert final["scope"] == "final"
        assert [ref["evaluation_id"] for ref in final["spec"]["factor_evaluations"]] == [
            results_by_expr[expr]["evaluation_id"] for expr in selected]
        validation = await completed(client, await call(client, "validate_research_evaluation", project_id=project_id,
                                                        evaluation_id=final["evaluation_id"]))
        assert validation["checks"]["strategy_oos"]["status"] == "passed"
        assert validation["selection_evaluation_id"] == strategy["evaluation_id"]
        assert validation["trial_count_scope"] == "frozen_round" and validation["observed_project_trials"] == 8
        assert final["validation"]["report_key"] == validation["report_key"]
        artifact = await call(client, "get_research_artifact", project_id=project_id,
                              artifact_id=final["validation_artifact"]["artifact_id"])
        assert artifact["payload"] == validation
        finished = await call(client, "get_research_round", **scope)
        assert finished["status"] == "completed" and finished["final_task"]["task_id"] == accepted["task_id"]
        assert finished["frozen"]["trial_manifest"]["manifest_hash"] == validation["trial_manifest_hash"]
        assert finished["frozen"]["trial_manifest"]["legacy_trials"] == 2

    reverse = server.model_copy(update={"env": {**env, "DATABASE_URL": f"sqlite+aiosqlite:///{tmp_path / 'reverse.sqlite'}"}})
    async with stdio_client(reverse) as streams, ClientSession(*streams) as client:
        await client.initialize()
        reversed_rows = {}
        for index in reversed(completion_order):
            published = await completed(client, await call(client, "submit_research_direction", **requests[index]))
            for expr, evaluation in zip(expressions[index], published["evaluations"], strict=True):
                reversed_rows[expr] = evaluation
            assert (await call(client, "finish_research_direction", **credentials[index]))["finished"]
        assert select_candidates(reversed_rows) == selected
        assert {key: r["evaluation_id"] for key, r in reversed_rows.items()} == {
            key: r["evaluation_id"] for key, r in results_by_expr.items()}
        again = await completed(client, await call(client, "submit_round_strategy", **coordinator, spec=spec))
        assert again["strategy_run_id"] == selection_id
        final_again = await completed(client, await call(client, "finalize_research_round", **coordinator,
                                                        selection_run_id=again["strategy_run_id"]))
        assert final_again["trial_manifest"] == final["trial_manifest"]
        assert final_again["validation_artifact"] == final["validation_artifact"]
        assert final_again["validation"] == validation
