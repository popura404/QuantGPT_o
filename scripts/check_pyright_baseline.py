"""Reject new Pyright diagnostics without hiding known, individually tracked debt."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = ROOT / "docs/testing/pyright-baseline.json"


def diagnostic_key(diagnostic: dict) -> tuple[str, str, str, str]:
    return tuple(diagnostic[field] for field in ("file", "severity", "rule", "message"))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--source-root", type=Path, default=ROOT, help="Source snapshot for initial debt capture.")
    parser.add_argument("--update", action="store_true", help="Explicitly regenerate reviewed debt; never run in CI.")
    parser.add_argument("--strict-path", action="append", default=[], help="Require zero diagnostics in this file/prefix.")
    args = parser.parse_args()
    source_root = args.source_root.resolve()
    result = subprocess.run(
        [sys.executable, "-m", "pyright", "--pythonpath", sys.executable, "--pythonversion", "3.12", "--outputjson", "quantgpt"],
        cwd=source_root, text=True, encoding="utf-8", capture_output=True, check=False,
    )
    if result.returncode not in (0, 1):
        print(result.stderr or result.stdout, file=sys.stderr)
        return result.returncode or 2
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(result.stderr or result.stdout, file=sys.stderr)
        return 2
    diagnostics = []
    for item in report["generalDiagnostics"]:
        path = Path(item["file"]).resolve().relative_to(source_root).as_posix()
        message = item["message"].replace(str(source_root), "<repository>").replace("\\", "/")
        diagnostics.append({
            "file": path, "severity": item["severity"], "rule": item.get("rule", "unknown"),
            "message": message, "line": item["range"]["start"]["line"] + 1,
        })
    current = Counter(diagnostic_key(item) for item in diagnostics)
    if args.update:
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        args.baseline.write_text(json.dumps({
            "schema_version": 1,
            "python_version": "3.12",
            "pyright_version": report["version"],
            "scope": "quantgpt",
            "note": "Historical diagnostics are debt, not accepted runtime behavior. Remove before G4.",
            "diagnostics": diagnostics,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {len(diagnostics)} individual diagnostics to {args.baseline}")
        return 0
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    if baseline["pyright_version"] != report["version"]:
        print("Pyright version differs from the baseline. Install the platform dependency lock.")
        return 2
    known = Counter(diagnostic_key(item) for item in baseline["diagnostics"])
    new = current - known
    resolved = known - current
    strict = [item for item in diagnostics if any(
        item["file"] == prefix.rstrip("/") or item["file"].startswith(prefix.rstrip("/") + "/")
        for prefix in args.strict_path
    )]
    for (path, severity, rule, message), count in new.items():
        print(f"NEW {path}: {severity} {rule} ({count}): {message}")
    for item in strict:
        print(f"STRICT {item['file']}:{item['line']}: {item['rule']}: {item['message']}")
    print(f"Pyright: {sum(current.values())} existing; {sum(new.values())} new; "
          f"{sum(resolved.values())} resolved; {len(strict)} in strict paths.")
    return int(bool(new or strict))


if __name__ == "__main__":
    raise SystemExit(main())
