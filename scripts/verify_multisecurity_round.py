"""Run a four-direction research round through independent MCP stdio clients on a frozen panel."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import secrets
import sys
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Proposal:
    name: str
    hypothesis: str
    expression: str
    fields: tuple[str, ...]


BASELINE = Proposal("baseline", "Ten-session momentum supplies a fixed comparator.",
                    "rank(close / ts_shift(close, 10))", ("close",))
PROPOSALS = (
    Proposal("trend", "Prices above their twenty-session mean may persist.",
             "rank(close / ts_mean(close, 20))", ("close",)),
    Proposal("reversal", "Stocks with weaker five-session returns may rebound.",
             "-rank(close / ts_shift(close, 5))", ("close",)),
    Proposal("low_volatility", "Less volatile recent daily returns may predict stronger forward returns.",
             "-rank(ts_std(close / ts_shift(close, 1), 10))", ("close",)),
    Proposal("volume_confirmation", "An intraday gain accompanied by elevated share volume may persist.",
             "rank((close / open - 1) * (volume / ts_mean(volume, 10)))", ("close", "open", "volume")),
)

SELECTION_RULE = {
    "statistic": "compound_net_return_of_fixed_direction_top_quantile",
    "source": "selection_period_factor_returns_artifact",
    "add_directions": 2,
    "tie_break": "evaluation_hash_ascending",
    "keep_baseline": True,
    "factor_weights": "equal",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


@asynccontextmanager
async def connection(server, log):
    with log.open("a", encoding="utf-8") as stderr:
        async with stdio_client(server, errlog=stderr) as streams, ClientSession(
            *streams, read_timeout_seconds=timedelta(minutes=3)
        ) as client:
            await client.initialize()
            yield client


async def call(client, tool_name, **arguments):
    reply = await client.call_tool(tool_name, arguments)
    if reply.isError or not reply.content:
        raise RuntimeError(f"{tool_name}: {reply.content[0].text if reply.content else 'empty response'}")
    result = json.loads(reply.content[0].text)
    if result.get("error_code"):
        raise RuntimeError(f"{tool_name}: {result.get('error_code')} {result.get('message') or result.get('hint', '')}")
    return result


async def wait_result(client, submitted):
    task_id = submitted.get("task_id")
    require(bool(task_id), f"Submission was not queued: {submitted.get('status')}")
    while True:
        task = await call(client, "get_mcp_task_status", task_id=task_id, include_result=True)
        if task["status"] == "completed":
            return task["result"]
        if task["status"] not in {"queued", "running"}:
            raise RuntimeError(f"Task {task_id}: {task['status']} {task.get('error')}")
        await asyncio.sleep(.5)


def factor(proposal: Proposal, currency: str) -> dict:
    from quantgpt.research.contracts import US_RESEARCH_FIELDS

    fields = []
    for name in proposal.fields:
        source = next(field for field in US_RESEARCH_FIELDS if field.name == name)
        field = source.model_dump(mode="json")
        if name in {"close", "open"}:
            field.update(currency=currency, unit=f"{currency}/share")
        fields.append(field)
    return {"expression": proposal.expression, "fields": fields}


def study_config(args, snapshot, calendar):
    from quantgpt.research.contracts import EvaluationConfigV1, SimulationConfigV1, content_hash
    from quantgpt.research.runtime import local_engine_identity

    days = sorted({str(day)[:10] for day in calendar if snapshot["start_session"] <= str(day)[:10] <= snapshot["end_session"]})
    selection = args.selection_sessions
    holdout = args.holdout_sessions
    need = 25 + selection + 5 + holdout
    require(len(days) >= need, f"Need {need} declared sessions, found {len(days)}")
    start, end = days[-(holdout + 5 + selection)], days[-(holdout + 6)]
    dates = [start, end, days[-holdout], days[-1]]
    simulation = SimulationConfigV1(rebalance_anchor_session=days[-need], rebalance_every_sessions=5,
                                    currency=args.currency, fees_bps=10)
    scope = {"market": args.market, "currency": args.currency, "timezone": args.timezone, "universe_id": args.universe,
             "universe_mode": "fixed_cohort", "universe_version": args.universe_version or snapshot["manifest_id"], "benchmark": args.benchmark,
             "calendar_id": args.calendar, "calendar_version": args.calendar_version}
    config = EvaluationConfigV1.model_validate({"backend": "local", "scope": scope, "direction": "higher_is_better",
        "window": {"start_session": start, "end_session": end, "warmup_observations": 25, "split_id": "four-direction-study",
                   "split_hash": content_hash("quantgpt.multisecurity_study/v2", [snapshot["manifest_id"], dates, SELECTION_RULE]),
                   "label_horizon_sessions": 5, "purge_sessions": 5}, "simulation_config": simulation.model_dump(mode="json"),
        "data_inputs": [snapshot["data_input"]], "engine": local_engine_identity()})
    return config.model_dump(mode="json"), dates, [day for day in days if start <= day <= end]


def specification(config, components, top_n):
    from quantgpt.research.contracts import SEMANTICS_VERSION

    return {"schema_version": "strategy_spec/v2", "name": "Four-direction selection study", "asset_class": "equity",
        "market": config["scope"]["market"], "universe": config["scope"]["universe_id"], "frequency": "daily",
        "semantics_version": SEMANTICS_VERSION, "simulation_config": config["simulation_config"],
        "factors": [{"id": proposal.name, "expression": proposal.expression, "direction": config["direction"],
                     "weight": 1 / len(components)} for proposal, _ in components],
        "factor_evaluations": [{**{k: result[k] for k in ("project_id", "evaluation_id", "evaluation_hash",
            "definition_hash", "backend")}, "scope": "selection"} for _, result in components],
        "signal_rules": {"type": "rank_threshold", "top_n": top_n},
        "portfolio_rule": {"weighting": "equal_weight", "rebalance_period": 5},
        "risk_rules": {"allow_short": False, "max_asset_weight": 1 / top_n, "max_turnover": 2},
        "cost_model": {"type": "fixed_bps", "bps": 10}, "validation": {"min_history_days": 30},
        "outputs": {"report": True, "signal_export": False}}


async def selection_score(client, row, scored_days):
    require(row["summary"]["phase"] == "selection" and row["summary"]["metrics"]["cost_adjusted"],
            "Selection requires a cost-adjusted selection evaluation")
    ref, = [item for item in row["summary"]["artifacts"] if item["kind"] == "returns"]
    artifact = await call(client, "get_research_artifact", project_id=row["project_id"], artifact_id=ref["artifact_id"])
    require(artifact["content_sha256"] == ref["content_sha256"]
            and artifact["metadata"]["evaluation_hash"] == row["evaluation_hash"], "Selection return artifact mismatch")
    records = artifact["payload"]
    require([item["session"][:10] for item in records] == scored_days, "Selection returns do not cover the declared sessions")
    returns = [item["return"] for item in records]
    require(all(isinstance(value, (int, float)) and math.isfinite(value) and value >= -1 for value in returns),
            "Selection returns contain unavailable or invalid values")
    return {"net_return": math.prod(1 + value for value in returns) - 1, "sessions": len(returns), "artifact": ref}


async def explore(server, log, start, common, branch, proposal, currency, scored_days):
    async with connection(server, log) as client:
        cred = {**common, "direction_id": branch["direction_id"], "direction_key": branch["direction_key"]}
        await start.wait()
        proposal_check = await call(client, "check_research_factor_novelty", project_id=common["project_id"],
                                    expressions=[proposal.expression])
        require(proposal_check["new_indices"] == [0], f"Known candidate in {proposal.name}")
        request = {**cred, "definitions": [factor(proposal, currency)], "only_new": True, "idempotency_key": proposal.name}
        submitted = await call(client, "submit_research_direction", **request)
        result = await wait_result(client, submitted)
        row = result["evaluations"][0]
        require(row["summary"].get("performance_observed") is True, f"{proposal.name}: {row.get('failure_reason')}")
        score = await selection_score(client, row, scored_days)
        retry = await call(client, "submit_research_direction", **request)
        require(retry["task_id"] == submitted["task_id"], "Direction retry changed task identity")
        await call(client, "finish_research_direction", **cred)
        return proposal, row, submitted["task_id"], score


async def verify(args):
    run_dir = args.work_dir.resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    db = run_dir / "research.sqlite"
    require(not db.exists(), f"Choose a fresh work directory; database already exists at {db}")
    env = {"DATABASE_URL": f"sqlite+aiosqlite:///{db.as_posix()}", "QUANTGPT_RESEARCH_SNAPSHOT_ROOT": str(run_dir / "snapshots"),
           "QUANTGPT_CACHE_ONLY": "true", "QUANTGPT_TASK_BACKEND": "thread", "AUTH_DISABLED": "false",
           "JWT_SECRET_KEY": secrets.token_urlsafe(48), "QUANTGPT_ADMIN_PASSWORD": secrets.token_urlsafe(32)}
    if args.import_root:
        env["QUANTGPT_RESEARCH_IMPORT_ROOT"] = str(args.import_root.resolve())
    evidence_root = args.evidence_root or os.environ.get("QUANTGPT_RESEARCH_EVIDENCE_ROOT")
    if evidence_root:
        env["QUANTGPT_RESEARCH_EVIDENCE_ROOT"] = str(Path(evidence_root).resolve())
    server = StdioServerParameters(command=sys.executable, args=["-B", "-m", "quantgpt", "--transport", "stdio"],
                                   cwd=ROOT, env=env)
    async with connection(server, run_dir / "coordinator.log") as client:
        project = await call(client, "create_research_project", name="Multi-security research acceptance", market=args.market)
        project_id = project["id"]
        if args.demo:
            demo = await call(client, "prepare_research_demo", project_id=project_id)
            manifest_id = demo["config"]["data_inputs"][0]["manifest_id"]
        else:
            manifest_id = args.manifest_id
        snapshot = await call(client, "register_research_snapshot", project_id=project_id, manifest_id=manifest_id)
        require(snapshot["declared_synthetic"] == args.demo, "Snapshot classification disagrees with requested input mode")
        require(snapshot["securities"] >= 5, "Five distinct securities are required by the factor quantile evaluator")
        manifest = json.loads((run_dir / "snapshots" / manifest_id / "manifest.json").read_text(encoding="utf-8"))
        config, dates, scored_days = study_config(args, snapshot, manifest["source_metadata"]["input_attributes"]["calendar_sessions"])
        top_n = min(5, max(2, snapshot["securities"] // 3))
        await call(client, "register_research_holdout", project_id=project_id, start=dates[2], end=dates[3])
        await call(client, "register_research_hypothesis", project_id=project_id, definition=factor(BASELINE, args.currency),
                   economic_hypothesis=BASELINE.hypothesis)
        baseline_factor = (await wait_result(client, await call(client, "evaluate_factors", project_id=project_id,
            definitions=[factor(BASELINE, args.currency)], config=config, idempotency_key="baseline-factor")))["evaluations"][0]
        require(baseline_factor["summary"].get("performance_observed") is True, f"Baseline: {baseline_factor.get('failure_reason')}")
        baseline = await wait_result(client, await call(client, "run_research_strategy", project_id=project_id,
            spec=specification(config, [(BASELINE, baseline_factor)], top_n), config=config, idempotency_key="baseline-strategy"))
        created = await call(client, "create_research_round", project_id=project_id, config=config,
            holdout_start=dates[2], holdout_end=dates[3], baseline_run_id=baseline["strategy_run_id"],
            directions=[{"name": p.name, "hypothesis": p.hypothesis} for p in PROPOSALS])
        common = {"project_id": project_id, "round_id": created["round_id"]}
        coordinator = {**common, "coordinator_key": created["coordinator_key"]}
        known = await call(client, "check_research_factor_novelty", project_id=project_id, expressions=[BASELINE.expression])
        require(known["new_indices"] == [], "Saved baseline was not recognized by the novelty lookup")
        start = asyncio.Barrier(len(PROPOSALS))
        async with asyncio.TaskGroup() as workers:
            jobs = [workers.create_task(explore(server, run_dir / f"{p.name}.log", start, common, b, p, args.currency, scored_days))
                    for b, p in zip(created["directions"], PROPOSALS, strict=True)]
            peak = 0
            while not all(job.done() for job in jobs):
                view = await call(client, "get_research_round", **common)
                concurrent = sum(task["status"] == "running" for branch in view["directions"] for task in branch["tasks"])
                peak = max(peak, concurrent)
                await asyncio.sleep(.3)
        rows = [j.result() for j in jobs]
        require(peak >= 2, "No overlapping direction tasks were observed by the coordinator")
        selected = sorted(rows, key=lambda item: (-item[3]["net_return"], item[1]["evaluation_hash"]))[:SELECTION_RULE["add_directions"]]
        spec = specification(config, [(BASELINE, baseline_factor)] + [(p, row) for p, row, _, _ in selected], top_n)
        strategy = await wait_result(client, await call(client, "submit_round_strategy", **coordinator, spec=spec))
        comparison = await call(client, "compare_research_strategies_to_baseline", **common,
                                augmented_run_id=strategy["strategy_run_id"])
        finalized = await call(client, "finalize_research_round", **coordinator, selection_run_id=strategy["strategy_run_id"])
        final = await wait_result(client, finalized)
        retried_final = await call(client, "finalize_research_round", **coordinator, selection_run_id=strategy["strategy_run_id"])
        require(retried_final["task_id"] == finalized["task_id"], "Final retry changed the frozen task")
        state = await call(client, "get_research_round", **common)
        manifest = state["frozen"]["trial_manifest"]
        report = final["validation"]
        if evidence_root:
            sidecar = Path(evidence_root) / manifest_id / "evidence.json"
            if sidecar.is_file():
                revision = hashlib.sha256(sidecar.read_bytes()).hexdigest()
                require(report["external_acceptance"].get("evidence_revision") == revision
                        and bool(report["external_acceptance"].get("checks")),
                        "Final validation does not carry the checks for the published evidence revision")
        expected = {baseline_factor["evaluation_id"], baseline["evaluation_id"], strategy["evaluation_id"],
                    *(row["evaluation_id"] for _, row, _, _ in rows)}
        require(state["status"] == "completed" and set(manifest["evaluation_ids"]) == expected
                and manifest["observed_trials"] == len(expected) == 7, "Frozen denominator or round state differs")
        artifact = await call(client, "get_research_artifact", project_id=project_id,
                              artifact_id=final["validation_artifact"]["artifact_id"])
        require(artifact["payload"] == report and report["trial_manifest_hash"] == manifest["manifest_hash"],
                "Canonical validation artifact differs from the frozen result")
        decision = await call(client, "export_research_strategy", project_id=project_id, strategy_run_id=final["strategy_run_id"])
        return {"technical_acceptance": "completed", "project_id": project_id, "round_id": created["round_id"],
            "data": {key: snapshot[key] for key in ("manifest_id", "content_sha256", "source", "feed", "rows", "sessions",
                "securities", "start_session", "end_session", "adjustment", "declared_synthetic")}, "scope": config["scope"],
            "selection": dates[:2], "holdout": dates[2:], "baseline_run_id": baseline["strategy_run_id"],
            "baseline_identified_as_known": True, "independent_direction_connections": len(jobs),
            "peak_concurrently_running_direction_tasks": peak,
            "engine": config["engine"], "selection_rule": SELECTION_RULE, "split_hash": config["window"]["split_hash"],
            "rank_ic_minimum_securities": 10, "rank_ic_cohort_meets_minimum": snapshot["securities"] >= 10,
            "directions": [{"name": p.name, "hypothesis": p.hypothesis, "expression": p.expression, "evaluation_id": row["evaluation_id"],
                "task_id": task_id, "selection_score": score,
                "selected": p.name in {winner.name for winner, _, _, _ in selected}} for p, row, task_id, score in rows],
            "selection_comparison": {key: comparison[key] for key in ("net_total_return", "mean_daily_turnover",
                "signal_similarity", "artifact_ref")}, "selection_run_id": strategy["strategy_run_id"],
            "frozen_trial_count": manifest["observed_trials"], "trial_manifest_hash": manifest["manifest_hash"],
            "final_task_id": finalized["task_id"], "final_run_id": final["strategy_run_id"], "final_evaluation_id": final["evaluation_id"],
            "validation_policy": report["policy"], "report_key": report["report_key"],
            "final_metrics": {k: final["metrics"].get(k) for k in ("total_return", "sharpe", "max_drawdown", "turnover")},
            "checks": {k: v["status"] for k, v in report["checks"].items()},
            "trial_correction": report["checks"]["trial_correction"], "external_acceptance": report["external_acceptance"],
            "validation_artifact": final["validation_artifact"], "exported": decision["exported"],
            "export_blockers": decision["validation"].get("blockers", [])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--demo", action="store_true", help="Use the explicitly synthetic 20-security fixture")
    mode.add_argument("--manifest-id", help="Frozen real-equity snapshot in --import-root")
    parser.add_argument("--import-root", type=Path)
    parser.add_argument("--evidence-root", type=Path)
    parser.add_argument("--work-dir", type=Path, required=True, help="Fresh directory for the database, logs and summary.json")
    parser.add_argument("--market", default="us_equity")
    parser.add_argument("--universe", default="published_fixed_cohort")
    parser.add_argument("--universe-version", help="Server evidence cohort version; defaults to the immutable snapshot ID")
    parser.add_argument("--calendar", default="XNYS")
    parser.add_argument("--calendar-version", default="published_snapshot_calendar")
    parser.add_argument("--timezone", default="America/New_York")
    parser.add_argument("--currency", default="USD")
    parser.add_argument("--benchmark", default="unprovided_total_return_benchmark")
    parser.add_argument("--selection-sessions", type=int, default=75)
    parser.add_argument("--holdout-sessions", type=int, default=65)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    args = parser.parse_args()
    if args.manifest_id and not args.import_root:
        parser.error("--manifest-id requires --import-root")
    if args.selection_sessions < 30 or args.holdout_sessions < 60 or args.timeout_seconds < 1:
        parser.error("Specify at least 30 selection sessions, 60 final sessions and a positive timeout")
    if args.demo:
        args.market, args.calendar, args.universe = "demo_global_equity", "synthetic_weekdays", "demo_large"
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    started = time.monotonic()
    result = asyncio.run(asyncio.wait_for(verify(args), timeout=args.timeout_seconds))
    result["elapsed_seconds"] = round(time.monotonic() - started, 2)
    summary = json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False)
    (args.work_dir / "summary.json").write_text(summary + "\n", encoding="utf-8")
    print(summary)


if __name__ == "__main__":
    main()
