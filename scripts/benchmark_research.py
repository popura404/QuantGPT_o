"""Offline expression/service benchmarks; timings are never numerical correctness evidence."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import platform
import subprocess
import tempfile
import threading
import time
import types
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import psutil

ROOT = Path(__file__).resolve().parents[1]
EXPRESSIONS = ["rank(close)", "ts_mean(close, 20)", "ts_std(close, 20)", "ts_delta(close, 5)", "scale(close)"]


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True, encoding="utf-8").strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("expressions", "service"), default="expressions")
    parser.add_argument("--stocks", type=int)
    parser.add_argument("--sessions", type=int)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--seed", type=int, default=20261005)
    parser.add_argument("--source-ref", help="Load expression_parser.py from this Git revision for historical timing.")
    parser.add_argument("--label", default="development-unverified")
    parser.add_argument("--output", type=Path, default=ROOT / "benchmark-results/synthetic.json")
    args = parser.parse_args()
    args.stocks = 20 if args.stocks is None else args.stocks
    args.sessions = (180 if args.mode == "service" else 252) if args.sessions is None else args.sessions
    if min(args.stocks, args.sessions, args.repeats) < 1:
        parser.error("stocks, sessions and repeats must be positive")
    if args.mode == "service":
        if args.source_ref or (args.stocks, args.sessions, args.seed) != (20, 180, 20261005):
            parser.error("service mode uses the current runner and the fixed 20-security/180-session offline demo")
        report = asyncio.run(benchmark_service(args.repeats))
        report["label"] = args.label
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"output": str(args.output), "phases": report["phases"],
                          "verification": report["verification"], "peak_rss_bytes": report["peak_rss_bytes"]}))
        return
    if args.source_ref:
        revision = git("rev-parse", args.source_ref)
        source = git("show", f"{revision}:quantgpt/expression_parser.py")
        module = types.ModuleType("quantgpt._benchmark_parser")
        module.__package__ = "quantgpt"
        exec(compile(source, f"{revision}:expression_parser.py", "exec"), module.__dict__)
        parse = module.parse_expression
    else:
        from quantgpt.expression_parser import parse_expression
        parse = parse_expression
        revision = git("rev-parse", "HEAD")
        source = (ROOT / "quantgpt/expression_parser.py").read_text(encoding="utf-8")
    rng = np.random.default_rng(args.seed)
    size = args.stocks * args.sessions
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, (args.stocks, args.sessions)), axis=1)).ravel()
    panel = pd.DataFrame({
        "stock_code": np.repeat([f"S{i:04d}" for i in range(args.stocks)], args.sessions),
        "trade_date": np.tile(pd.bdate_range("2020-01-01", periods=args.sessions), args.stocks),
        "close": close, "open": close, "high": close * 1.01, "low": close * 0.99,
        "volume": rng.integers(1000, 100000, size=size).astype(float),
    })
    process = psutil.Process()
    peak_rss = [process.memory_info().rss]
    finished = threading.Event()

    def sample_memory() -> None:
        while not finished.wait(0.01):
            peak_rss[0] = max(peak_rss[0], process.memory_info().rss)

    sampler = threading.Thread(target=sample_memory, daemon=True)
    sampler.start()
    samples = []
    try:
        with tempfile.TemporaryDirectory(prefix="quantgpt-benchmark-") as directory:
            data_path = Path(directory) / "synthetic.parquet"
            panel.to_parquet(data_path, index=False)
            data_bytes = data_path.stat().st_size
            digest = hashlib.sha256(data_path.read_bytes()).hexdigest()
            compiled = [parse(expression) for expression in EXPRESSIONS]
            for phase in ("cold_application", "warm_panel"):
                for _ in range(args.repeats):
                    begin = time.perf_counter()
                    if phase == "cold_application":
                        data = pd.read_parquet(data_path)
                        functions = [parse(expression) for expression in EXPRESSIONS]
                    else:
                        data, functions = panel, compiled
                    read_done = time.perf_counter()
                    summaries = []
                    for expression, function in zip(EXPRESSIONS, functions):
                        values = function(data)
                        summaries.append({"expression": expression, "rows": len(values), "non_null": int(values.notna().sum())})
                    compute_done = time.perf_counter()
                    payload = json.dumps(summaries, separators=(",", ":")).encode()
                    end = time.perf_counter()
                    samples.append({
                        "phase": phase, "seconds": end - begin,
                        "read_compile_seconds": read_done - begin,
                        "compute_seconds": compute_done - read_done,
                        "serialization_seconds": end - compute_done,
                        "panel_reads": int(phase == "cold_application"),
                        "read_bytes": data_bytes if phase == "cold_application" else 0,
                        "expression_compiles": len(EXPRESSIONS) if phase == "cold_application" else 0,
                        "expression_evaluations": len(EXPRESSIONS), "provider_requests": 0,
                        "summary_bytes": len(payload),
                    })
    finally:
        finished.set()
        sampler.join()
    report = {
        "schema_version": 1, "label": args.label, "dataset_kind": "synthetic",
        "correctness_oracle": False,
        "known_limitations": [
            "Business-day generator is not an exchange calendar.",
            "Cold application reads do not flush the operating-system file cache.",
            "This measures expression evaluation only, not service/provider/backtest performance.",
            "Legacy parser timing includes known incorrect semantics when source-ref is the old baseline.",
        ],
        "source_revision": revision, "source_from_git": bool(args.source_ref),
        "working_tree_dirty": bool(git("status", "--porcelain", "--untracked-files=no")),
        "parser_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "environment": {"python": platform.python_version(), "platform": platform.platform(),
                        "machine": platform.machine(), "logical_cpus": psutil.cpu_count(),
                        "packages": {name: importlib.metadata.version(name) for name in ("pandas", "numpy", "pyarrow", "psutil")}},
        "config": {"stocks": args.stocks, "sessions": args.sessions, "repeats": args.repeats,
                   "seed": args.seed, "expressions": EXPRESSIONS},
        "data_sha256": digest, "data_bytes": data_bytes,
        "peak_rss_bytes": peak_rss[0], "peak_rss_sampling_seconds": 0.01,
        "phases": {phase: {"p50_seconds": float(np.percentile([s["seconds"] for s in samples if s["phase"] == phase], 50)),
                           "p95_seconds": float(np.percentile([s["seconds"] for s in samples if s["phase"] == phase], 95))}
                   for phase in ("cold_application", "warm_panel")},
        "samples": samples,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "phases": report["phases"], "peak_rss_bytes": peak_rss[0]}))


async def benchmark_service(repeats: int) -> dict:
    """Measure the actual authorized SQLite evaluation service and immutable retry."""
    import sqlite3

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import quantgpt.backtest as backtest_module
    import quantgpt.research.evaluations as evaluation_module
    from quantgpt.models import Base, ResearchAuditEvent, User
    from quantgpt.research.contracts import EvaluationConfigV1, FactorDefinitionV1, canonical_json, content_hash
    from quantgpt.research.demo import prepare_offline_demo
    from quantgpt.research.projects import create_project
    from quantgpt.research.runtime import local_engine_identity

    base = ROOT / "benchmark-results"
    base.mkdir(exist_ok=True)
    # Preserve isolated runs for diagnosis; never connect to the user's database.
    directory = Path(tempfile.mkdtemp(prefix="service-", dir=base))
    snapshots = directory / "snapshots"
    engine_before = local_engine_identity().model_dump(mode="json")
    setup_begin = time.perf_counter()
    demo = prepare_offline_demo(snapshots)
    config = EvaluationConfigV1.model_validate(demo["config"])
    template = demo["definitions"][0]
    expressions = [template["expression"], "-rank(close / delay(close, 5) - 1)",
                   "-rank(ts_std(close / delay(close, 1) - 1, 20))"]
    definitions = [FactorDefinitionV1.model_validate({**template, "expression": expression}) for expression in expressions]
    snapshot_id = config.data_inputs[0].manifest_id
    assert snapshot_id is not None
    panel_path = snapshots / snapshot_id / "market.parquet"
    setup_seconds = time.perf_counter() - setup_begin
    process = psutil.Process()
    peak_rss = [process.memory_info().rss]
    finished = threading.Event()

    def sample_memory() -> None:
        while not finished.wait(.01):
            peak_rss[0] = max(peak_rss[0], process.memory_info().rss)

    sampler = threading.Thread(target=sample_memory, daemon=True)
    sampler.start()
    samples, integrity = [], []
    invalidation = {}
    try:
        for repetition in range(repeats):
            database_path = directory / f"run-{repetition}.sqlite"
            engine = create_async_engine("sqlite+aiosqlite:///" + database_path.as_posix())
            try:
                async with engine.begin() as connection:
                    await connection.run_sync(Base.metadata.create_all)
                factory = async_sessionmaker(engine, expire_on_commit=False)
                async with factory() as session:
                    actor_id = uuid.uuid4()
                    session.add(User(id=actor_id, email=f"offline-benchmark-{repetition}@example.invalid", is_active=True))
                    await session.flush()
                    project = await create_project(session, actor_id, name="Offline performance fixture", market=config.scope.market)
                    session.add(ResearchAuditEvent(project_id=project.id, actor_id=actor_id,
                        action="snapshot_registered", payload={"snapshot_id": snapshot_id, "source": "synthetic_offline_fixture"}))
                    await session.commit()
                    outputs = []
                    artifact_fingerprints = []
                    for phase in ("cold_batch", "warm_identical_retry"):
                        io_before = process.io_counters()
                        begin = time.perf_counter()
                        with (patch.object(evaluation_module, "load_evaluation_panel", wraps=evaluation_module.load_evaluation_panel) as loader,
                              patch.object(backtest_module, "run_factor_backtest", wraps=backtest_module.run_factor_backtest) as calculator):
                            result = await evaluation_module.evaluate_factor_batch(session, actor_id, project.id,
                                definitions, config, snapshot_root=snapshots)
                            observed_reads, observed_calls = loader.call_count, calculator.call_count
                        elapsed = time.perf_counter() - begin
                        io_after = process.io_counters()
                        if not all(row["summary"].get("performance_observed") for row in result["evaluations"]):
                            failures = [row.get("failure_reason") for row in result["evaluations"]]
                            raise RuntimeError(f"Benchmark evaluation failed: {failures}")
                        diagnostics = result["batch_diagnostics"]
                        expected_reads, expected_calls = (1, len(definitions)) if phase == "cold_batch" else (0, 0)
                        if (observed_reads, observed_calls) != (expected_reads, expected_calls):
                            raise AssertionError(f"Unexpected actual work in {phase}: {observed_reads} reads, {observed_calls} calls")
                        if diagnostics != {"panel_builds": observed_reads, "engine_calls": observed_calls}:
                            raise AssertionError("Service diagnostics disagree with independent instrumentation")
                        serialize_start = time.perf_counter()
                        encoded = canonical_json(result).encode("utf-8")
                        serialization_seconds = time.perf_counter() - serialize_start
                        fingerprints = []
                        artifact_bytes = 0
                        for row in result["evaluations"]:
                            for reference in row["summary"]["artifacts"]:
                                artifact = await evaluation_module.read_artifact(session, actor_id, project.id,
                                    uuid.UUID(reference["artifact_id"]))
                                fingerprints.append((reference["artifact_id"], artifact["content_sha256"]))
                                artifact_bytes += len(canonical_json(artifact["payload"]).encode("utf-8"))
                        artifact_fingerprints.append(sorted(fingerprints))
                        outputs.append(result["evaluations"])
                        samples.append({"phase": phase, "repetition": repetition, "seconds": elapsed,
                            "response_serialization_seconds": serialization_seconds, "response_bytes": len(encoded),
                            "artifact_payload_bytes": artifact_bytes, "provider_requests": 0,
                            "panel_builds": observed_reads, "engine_calls": observed_calls,
                            "logical_panel_read_bytes": panel_path.stat().st_size if observed_reads else 0,
                            "process_io_read_bytes": io_after.read_bytes - io_before.read_bytes,
                            "process_io_write_bytes": io_after.write_bytes - io_before.write_bytes,
                            "database_bytes_after_phase": database_path.stat().st_size,
                            "evaluation_hashes": [row["evaluation_hash"] for row in result["evaluations"]]})
                    same_results = canonical_json(outputs[0]) == canonical_json(outputs[1])
                    same_artifacts = artifact_fingerprints[0] == artifact_fingerprints[1]
                    if not same_results or not same_artifacts:
                        raise AssertionError("Identical retry changed evaluation results or artifact hashes")
                    integrity.append({"repetition": repetition, "evaluation_results_identical": same_results,
                                      "artifact_ids_and_hashes_identical": same_artifacts,
                                      "evaluation_result_sha256": content_hash("benchmark.result/v1", outputs[0]),
                                      "artifact_count": len(artifact_fingerprints[0])})
                    if repetition == 0:
                        changed = config.model_dump(mode="json")
                        changed["simulation_config"]["fees_bps"] += 1
                        changed_config = EvaluationConfigV1.model_validate(changed)
                        rerun = await evaluation_module.evaluate_factor_batch(session, actor_id, project.id,
                            definitions[:1], changed_config, snapshot_root=snapshots)
                        if rerun["batch_diagnostics"] != {"panel_builds": 1, "engine_calls": 1}:
                            raise AssertionError("Changed cost configuration incorrectly reused the original evaluation")
                        if rerun["evaluations"][0]["evaluation_hash"] == outputs[0][0]["evaluation_hash"]:
                            raise AssertionError("Changed cost configuration did not change evaluation identity")
                        restored = await evaluation_module.evaluate_factor_batch(session, actor_id, project.id,
                            definitions, config, snapshot_root=snapshots)
                        if restored["batch_diagnostics"] != {"panel_builds": 0, "engine_calls": 0}:
                            raise AssertionError("Changing costs invalidated an unrelated immutable original evaluation")
                        if canonical_json(restored["evaluations"]) != canonical_json(outputs[0]):
                            raise AssertionError("Changed-cost run mutated original results")
                        invalidation = {"changed_cost_recomputed": True, "changed_cost_has_new_identity": True,
                                        "original_evaluation_retained_and_reused": True,
                                        "outside_timed_samples": True}
            finally:
                await engine.dispose()
    finally:
        finished.set()
        sampler.join()
    engine_after = local_engine_identity().model_dump(mode="json")
    if engine_before != engine_after:
        raise RuntimeError("Source changed during the benchmark; repeat after edits finish")
    lock_name = {"Windows": "windows-py312.lock", "Linux": "linux-py312.lock"}.get(platform.system())
    lock_path = ROOT / "requirements" / lock_name if lock_name else None
    return {"schema_version": "research_service_benchmark/v1", "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset_kind": "synthetic", "correctness_oracle": False,
        "config": {"stocks": 20, "sessions": 180, "rows": 3600, "seed": 20261005,
                   "repeats": repeats, "expressions": expressions, "engine": "python"},
        "source_revision": git("rev-parse", "HEAD"),
        "working_tree_dirty": bool(git("status", "--porcelain", "--untracked-files=no")),
        "engine_identity": engine_before,
        "environment": {"python": platform.python_version(), "platform": platform.platform(),
            "machine": platform.machine(), "logical_cpus": psutil.cpu_count(), "sqlite": sqlite3.sqlite_version,
            "packages": {name: importlib.metadata.version(name) for name in
                ("pandas", "numpy", "pyarrow", "psutil", "sqlalchemy", "aiosqlite", "pydantic")},
            "dependency_lock": lock_name,
            "dependency_lock_sha256": hashlib.sha256(lock_path.read_bytes()).hexdigest() if lock_path else None},
        "dataset": {"snapshot_id": snapshot_id, "logical_content_sha256": config.data_inputs[0].content_sha256,
                    "parquet_sha256": hashlib.sha256(panel_path.read_bytes()).hexdigest(),
                    "parquet_bytes": panel_path.stat().st_size},
        "setup_seconds": setup_seconds, "peak_rss_bytes": peak_rss[0], "peak_rss_sampling_seconds": .01,
        "verification": {"all_retries_reused_results": True, "all_artifacts_verified": True,
                         "actual_read_compute_counters_match": True, "engine_source_stable_during_run": True,
                         "rust_conformance": "unverified; not benchmarked", "integrity": integrity,
                         "cache_invalidation": invalidation},
        "phases": {phase: {"p50_seconds": float(np.percentile([s["seconds"] for s in samples if s["phase"] == phase], 50)),
                           "p95_seconds": float(np.percentile([s["seconds"] for s in samples if s["phase"] == phase], 95))}
                   for phase in ("cold_batch", "warm_identical_retry")},
        "samples": samples, "isolated_run_directory": str(directory),
        "known_limitations": ["Synthetic weekday calendar and prices; no real-market validation.",
            "Cold means empty evaluation database; OS file cache is not flushed.",
            "Setup, artifact readback and database schema creation are outside service timings.",
            "Retry speed is reuse of immutable evidence, not a faster numerical engine.",
            "Historical pre-fix parser measurements are not a correctness oracle.",
            "Python correctness is established by separate golden/regression tests, not timing."]}


if __name__ == "__main__":
    main()
