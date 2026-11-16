"""Freeze a fixed US equity research sample from EODHD's documented public demo.

Run with `python -m scripts.freeze_us_public_sample --fetch` once, then omit
`--fetch` to reproduce from the original response bytes and receipts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from collections.abc import Callable
from datetime import date, datetime, timezone
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from urllib.parse import urlencode

import httpx
import numpy as np
import pandas as pd
from filelock import FileLock

from quantgpt.data_snapshots import freeze_market_frame
from quantgpt.research.contracts import ResearchScope
from quantgpt.us_data.calendar import NewYorkCalendar

SYMBOLS = ("AAPL", "AMZN", "MCD", "MSFT", "TSLA")
START = date(2023, 1, 3)
END = date(2024, 12, 31)
PROVIDER = "eodhd_public_demo"
COHORT = "eodhd_demo_us_equities"
ROOT = Path("data/us_public_eodhd")
DOCS = {
    "prices": "https://eodhd.com/help/methodology",
    "actions": "https://eodhd.com/financial-apis/api-splits-dividends",
    "access": "https://eodhd.com/financial-apis/quick-start-with-our-financial-data-apis",
}


def capture_response(root: Path, symbol: str, endpoint: str, url: str, suffix: str, *, fetch: bool,
                     validator: Callable[[bytes], bool], client: httpx.Client | None = None) -> tuple[bytes, dict]:
    directory = root / "sources"
    directory.mkdir(parents=True, exist_ok=True)
    name = f"{symbol}.{endpoint}"
    path, receipt_path = directory / f"{name}.{suffix}", directory / f"{name}.receipt.json"
    if path.is_file() and receipt_path.is_file():
        raw = path.read_bytes()
        receipt = json.loads(receipt_path.read_bytes())
        if receipt.get("url") != url or receipt.get("sha256") != hashlib.sha256(raw).hexdigest():
            raise ValueError(f"Cached response receipt differs from the request or data: {name}")
    else:
        if not fetch:
            raise FileNotFoundError(f"Missing response for {name}. Use --fetch to retrieve the public sample.")
        response = (client.get if client is not None else httpx.get)(url, timeout=25)
        response.raise_for_status()
        raw = response.content
        if not validator(raw):
            raise ValueError(f"Unexpected {name} response")
        receipt = {"url": url, "sha256": hashlib.sha256(raw).hexdigest(), "ticker": symbol, "endpoint": endpoint,
                   "retrieved_at": datetime.now(timezone.utc).isoformat(), "source_file": f"sources/{path.name}"}
        path.write_bytes(raw)
        receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    return raw, receipt


def source(root: Path, symbol: str, endpoint: str, fetch: bool, *, end: date = END) -> tuple[list[dict], dict]:
    params = {"from": START.isoformat(), "fmt": "json", "api_token": "demo"}
    if endpoint != "splits":
        params["to"] = end.isoformat()
    url = f"https://eodhd.com/api/{endpoint}/{symbol}.US?{urlencode(params)}"
    raw, receipt = capture_response(root, symbol, endpoint, url, "json", fetch=fetch,
                                    validator=lambda data: isinstance(json.loads(data), list))
    return json.loads(raw), receipt


def parse_symbol(symbol: str, bars: list[dict], splits: list[dict], dividends: list[dict],
                 expected: set[str]) -> tuple[list[dict], list[dict]]:
    security_id = f"eodhd:{symbol}.US"
    ratios = {}
    actions = []
    for event in splits:
        day = date.fromisoformat(event["date"])
        numerator, denominator = str(event["split"]).replace(":", "/").split("/")
        ratio = Fraction(Decimal(numerator)) / Fraction(Decimal(denominator))
        if day in ratios or ratio <= 0:
            raise ValueError(f"Invalid split for {symbol} on {day}")
        ratios[day] = ratio
        if day.isoformat() in expected:
            actions.append({"event_id": f"{security_id}:split:{day}", "security_id": security_id, "type": "split",
                            "session": day.isoformat(), "ratio": float(ratio)})
    for event in dividends:
        day = date.fromisoformat(event["date"])
        payment = date.fromisoformat(event["paymentDate"])
        amount = float(event["unadjustedValue"])
        if day.isoformat() not in expected or payment < day or event["currency"] != "USD" or not np.isfinite(amount) or amount <= 0:
            raise ValueError(f"Unexpected cash dividend for {symbol} on {day}")
        actions.append({"event_id": f"{security_id}:div:{day}:{payment}:{amount}", "security_id": security_id,
                        "type": "cash_dividend", "session": day.isoformat(), "pay_session": payment.isoformat(),
                        "per_share": amount})
    if len({event["event_id"] for event in actions}) != len(actions):
        raise ValueError(f"Duplicate corporate action for {symbol}")
    if len(bars) != len(expected) or {bar["date"] for bar in bars} != expected:
        raise ValueError(f"Missing, duplicate or unexpected exchange sessions for {symbol}")
    rows = []
    for bar in bars:
        day = date.fromisoformat(bar["date"])
        volume = Fraction(Decimal(str(bar["volume"])))
        for split_date, ratio in ratios.items():
            if split_date > day:
                volume /= ratio
        prices = {field: float(bar[field]) for field in ("open", "high", "low", "close")}
        if (not np.isfinite(list(prices.values())).all() or prices["low"] <= 0 or volume <= 0 or volume.denominator != 1
                or not prices["low"] <= min(prices["open"], prices["close"]) <= max(prices["open"], prices["close"]) <= prices["high"]):
            raise ValueError(f"Invalid raw OHLC or as-traded share volume for {symbol} on {day}")
        rows.append({"stock_code": security_id, "security_id": security_id, "ticker": symbol, "trade_date": pd.Timestamp(day),
                     "session": day.isoformat(), **prices, "volume": int(volume), "adjustment": "raw", "currency": "USD"})
    return rows, actions


def store_evidence(root: Path, bundle: dict, sessions: list[str], benchmark_source_root: Path | None) -> None:
    if benchmark_source_root is not None:
        from scripts.freeze_us_benchmark import build_benchmark

        benchmark = build_benchmark(benchmark_source_root, sessions)
        if benchmark["benchmark_id"] != bundle["scope"]["benchmark"]:
            raise ValueError("Benchmark source differs from the declared research scope")
        bundle["benchmark"] = benchmark
    directory = root / "evidence" / bundle["manifest_id"]
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / "evidence.json"
    with FileLock(str(directory / ".evidence.lock")):
        if destination.exists():
            existing = json.loads(destination.read_bytes())
            if "benchmark" not in bundle and "benchmark" in existing:
                bundle["benchmark"] = existing["benchmark"]
            if existing == bundle:
                return
            if "benchmark" in existing or "benchmark" not in bundle or {**existing, "benchmark": bundle["benchmark"]} != bundle:
                raise ValueError("An existing evidence bundle differs from the parsed capture. Preserve it and use a fresh output root.")
        with tempfile.NamedTemporaryFile(mode="w", dir=directory, encoding="utf-8", delete=False) as temporary:
            json.dump(bundle, temporary, allow_nan=False, sort_keys=True)
        Path(temporary.name).replace(destination)


def freeze_sample(root: Path, symbols: tuple[str, ...], *, fetch: bool, end: date = END, benchmark: str = "none",
                  benchmark_source_root: Path | None = None) -> dict:
    calendar = NewYorkCalendar()
    sessions = [session.session.isoformat() for session in calendar.sessions(START, end)]
    rows, actions, receipts = [], [], []
    for symbol in symbols:
        data = {}
        for endpoint in ("eod", "splits", "div"):
            data[endpoint], receipt = source(root, symbol, endpoint, fetch, end=end)
            receipts.append(receipt)
        bars, events = parse_symbol(symbol, data["eod"], data["splits"], data["div"], set(sessions))
        rows.extend(bars)
        actions.extend(events)
    frame = pd.DataFrame(rows).sort_values(["trade_date", "stock_code"]).reset_index(drop=True)
    frame["trade_date"] = frame["trade_date"].astype("datetime64[ns]")
    frame.attrs.update(calendar_sessions=sessions, calendar_version=calendar.version, adjustment="raw",
                       corporate_actions=sorted(actions, key=lambda row: (row["session"], row["event_id"])),
                       research_only=True, capability_blockers=["source_publication_time_and_vintages_unverified",
                       "historical_membership_unverified", "delisting_terminal_values_unverified",
                       "corporate_action_coverage_unverified", "total_return_benchmark_unverified"])
    query = {"feed": "eodhd/eod", "cohort": COHORT, "symbols": list(symbols), "from": START.isoformat(), "to": end.isoformat()}
    if benchmark != "none":
        query["benchmark_id"] = benchmark
    manifest = freeze_market_frame(frame, root / "imports", vendor=PROVIDER, query_params=query, source_metadata={
        "documentation": DOCS, "source_responses": receipts, "cohort_security_ids": [f"eodhd:{symbol}.US" for symbol in symbols],
        "calendar_version": calendar.version, "security_identity": "provider ticker within this fixed sample only",
        "availability": "downloaded historical bars; per-session publication timestamps unavailable",
        "usage": "provider public API demo for local testing; no independent licensed-use attestation",
        "source_ohlc_basis": "as_traded", "source_volume_basis": "split_adjusted",
        "volume_conversion": "divide each reported volume by every later split ratio in the provider response"})
    # These rows come directly from parsed vendor responses, independent of
    # the stored Parquet. A public demo is not a reviewed licence attestation.
    scope = ResearchScope(market="us_equity", currency="USD", universe_id=COHORT, universe_mode="fixed_cohort",
                          universe_version="eodhd-public-demo-fixed/v1", calendar_id="XNYS",
                          calendar_version=calendar.version, timezone=calendar.timezone, benchmark=benchmark)
    release = hashlib.sha256(json.dumps([item["sha256"] for item in receipts]).encode("ascii")).hexdigest()
    provenance = {"provider": PROVIDER, "dataset": "eodhd/eod", "release": f"local_response_capture/sha256/{release}",
                  "source_ref": DOCS["prices"], "source_responses": receipts,
                  "access_documentation": DOCS["access"], "access_status": "public_demo_use_only",
                  "upstream_revision": "unverified"}
    price_fields = ("security_id", "session", "open", "high", "low", "close", "volume")
    bundle = {"schema_version": "external_market_evidence/v1", "manifest_id": manifest["snapshot_id"],
              "content_sha256": manifest["content_hash"].removeprefix("sha256:"), "scope": scope.model_dump(mode="json"),
              "coverage_start": START.isoformat(), "coverage_end": end.isoformat(),
              "prices": {"provenance": provenance, "adjustment": "raw", "currency": "USD",
                         "rows": [{k: row[k] for k in price_fields} for row in rows]},
              "corporate_actions": {"provenance": {**provenance, "dataset": "eodhd/div+splits", "source_ref": DOCS["actions"]},
                                    "coverage": [{"security_id": f"eodhd:{symbol}.US", "start": START.isoformat(), "end": end.isoformat(),
                                                  "event_types": ["cash_dividend", "split"]} for symbol in symbols],
                                    "events": actions}}
    store_evidence(root, bundle, sessions, benchmark_source_root)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true", help="Retrieve missing demo responses from eodhd.com.")
    parser.add_argument("--symbols", nargs="+", choices=SYMBOLS, default=SYMBOLS)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--end", type=date.fromisoformat, default=END, help="End of study. Use a fresh --root when changing the window.")
    parser.add_argument("--benchmark", default="none", help="Predeclared benchmark identifier.")
    parser.add_argument("--benchmark-source-root", type=Path, help="Attach the independently captured NASDAQXNDX benchmark.")
    args = parser.parse_args()
    selected = tuple(sorted(set(args.symbols)))
    if len(selected) < 2:
        parser.error("Choose at least two different US equity symbols.")
    manifest = freeze_sample(args.root, selected, fetch=args.fetch, end=args.end, benchmark=args.benchmark,
                             benchmark_source_root=args.benchmark_source_root)
    print(json.dumps({"manifest_id": manifest["snapshot_id"], "rows": manifest["row_count"], "symbols": selected,
                      "coverage_start": START.isoformat(), "coverage_end": args.end.isoformat(), "research_only": True,
                      "import_root": str((args.root / "imports").resolve()), "evidence_root": str((args.root / "evidence").resolve())}))
