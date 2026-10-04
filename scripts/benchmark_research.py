"""Offline synthetic expression benchmark; numeric outputs are never correctness evidence."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import subprocess
import tempfile
import threading
import time
import types
from pathlib import Path

import numpy as np
import pandas as pd
import psutil

ROOT = Path(__file__).resolve().parents[1]
EXPRESSIONS = ["rank(close)", "ts_mean(close, 20)", "ts_std(close, 20)", "ts_delta(close, 5)", "scale(close)"]


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True, encoding="utf-8").strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stocks", type=int, default=20)
    parser.add_argument("--sessions", type=int, default=252)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--seed", type=int, default=20261005)
    parser.add_argument("--source-ref", help="Load expression_parser.py from this Git revision for historical timing.")
    parser.add_argument("--label", default="development-unverified")
    parser.add_argument("--output", type=Path, default=ROOT / "benchmark-results/synthetic.json")
    args = parser.parse_args()
    if min(args.stocks, args.sessions, args.repeats) < 1:
        parser.error("stocks, sessions and repeats must be positive")
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


if __name__ == "__main__":
    main()
