"""Freeze 15 US equities from Kibot's documented public guest daily-data API.

Prepare EODHD's five-symbol dividend/split source first with
`python -m scripts.freeze_us_public_sample --fetch --end 2025-12-31 --root data/us_public_eodhd_2025`.
Then run `python -m scripts.freeze_kibot_us_sample --fetch`. Omit --fetch for offline replay.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
from datetime import date
from pathlib import Path
from typing import cast
from urllib.parse import urlencode

import httpx
import numpy as np
import pandas as pd

from quantgpt.data_snapshots import freeze_market_frame
from quantgpt.research.contracts import ResearchScope
from quantgpt.us_data.calendar import NewYorkCalendar
from scripts import freeze_us_public_sample as eodhd

SYMBOLS = ("AAPL", "ABNB", "ADBE", "AMD", "AMZN", "DASH", "DDOG", "MCD", "MSFT",
           "PINS", "PLTR", "ROKU", "SNOW", "TSLA", "UBER")
ROOT = Path("data/us_public_kibot_2025")
END = date(2025, 12, 31)
DOC = "https://www.kibot.com/api/history-request.html"
ACCESS = "https://www.kibot.com/api/api-access.html"


def daily_bars(client: httpx.Client, root: Path, symbol: str, dates: pd.DatetimeIndex, *,
               fetch: bool, raw_prices: bool) -> tuple[pd.DataFrame, dict]:
    params = {"action": "history", "symbol": symbol, "interval": "daily", "startdate": eodhd.START.strftime("%m/%d/%Y"),
              "enddate": END.strftime("%m/%d/%Y"), "unadjusted": "1" if raw_prices else "0", "type": "stocks"}
    url = f"https://api.kibot.com/?{urlencode(params)}"
    kind = "unadjusted" if raw_prices else "fully_adjusted"
    body, receipt = eodhd.capture_response(root, symbol, kind, url, "csv", fetch=fetch, client=client,
        validator=lambda data: bool(re.match(rb"\d{2}/\d{2}/\d{4},", data)))
    bars = pd.read_csv(io.BytesIO(body), header=None, names=["trade_date", "open", "high", "low", "close", "volume"])
    bars["trade_date"] = pd.to_datetime(bars["trade_date"], format="%m/%d/%Y").astype("datetime64[ns]")
    bars = bars.sort_values("trade_date").reset_index(drop=True)
    if not pd.DatetimeIndex(bars["trade_date"]).equals(dates):
        raise ValueError(f"Unexpected or missing trading days for {symbol} {kind}")
    values = bars[["open", "high", "low", "close", "volume"]]
    if (not np.isfinite(values.to_numpy()).all() or (values.to_numpy() <= 0).any()
            or not (bars["high"] >= bars[["open", "close", "low"]].max(axis=1)).all()
            or not (bars["low"] <= bars[["open", "close", "high"]].min(axis=1)).all()
            or (cast(pd.Series, bars["volume"]).to_numpy() % 1 != 0).any()):
        raise ValueError(f"Invalid OHLC or share volume for {symbol} {kind}")
    return bars, receipt


def freeze_sample(root: Path, action_root: Path, *, fetch: bool, benchmark: str, benchmark_source_root: Path | None = None) -> dict:
    calendar = NewYorkCalendar()
    labels = [s.session.isoformat() for s in calendar.sessions(eodhd.START, END)]
    dates = pd.DatetimeIndex(labels, dtype="datetime64[ns]")
    receipts, action_receipts, frames, events, diagnostics = [], [], [], [], {}
    with httpx.Client(timeout=25) as client:
        if fetch:
            login = client.get("https://api.kibot.com/", params={"action": "login", "user": "guest", "password": "guest"})
            login.raise_for_status()
            if not login.text.startswith("200 OK"):
                raise ValueError("The provider did not accept its public guest login")
        for symbol in SYMBOLS:
            bars, raw_receipt = daily_bars(client, root, symbol, dates, fetch=fetch, raw_prices=True)
            adjusted, adj_receipt = daily_bars(client, root, symbol, dates, fetch=fetch, raw_prices=False)
            receipts.extend([raw_receipt, adj_receipt])
            adjustment_ratio = adjusted["close"] / bars["close"]
            diagnostics[symbol] = {"raw_and_fully_adjusted_close_identical": bool(bars["close"].eq(adjusted["close"]).all()),
                                   "max_daily_change_in_adjustment_ratio": float(adjustment_ratio.pct_change().abs().max())}
            asset = f"kibot:{symbol}"
            frames.append(bars.assign(stock_code=asset, security_id=asset, ticker=symbol, session=labels, adjustment="raw", currency="USD"))
            if symbol in eodhd.SYMBOLS:
                data = {}
                for endpoint in ("eod", "splits", "div"):
                    data[endpoint], receipt = eodhd.source(action_root, symbol, endpoint, fetch=False, end=END)
                    action_receipts.append(receipt)
                _, actions = eodhd.parse_symbol(symbol, data["eod"], data["splits"], data["div"], set(labels))
                events.extend({**action, "security_id": asset} for action in actions)
    panel = pd.concat(frames, ignore_index=True).sort_values(["trade_date", "stock_code"]).reset_index(drop=True)
    panel.attrs.update(calendar_sessions=labels, calendar_version=calendar.version, adjustment="raw", research_only=True,
                       corporate_actions=sorted(events, key=lambda entry: (entry["session"], entry["event_id"])),
                       capability_blockers=["upstream_vintage_or_dissemination_unverified", "historical_universe_unverified",
                                            "partial_corporate_actions_and_delisting_coverage", "license_not_independently_reviewed"])
    scope = ResearchScope(market="us_equity", currency="USD", universe_id="kibot_guest_us_equities", universe_mode="fixed_cohort",
                          universe_version="kibot-guest-fixed/v1", calendar_id="XNYS", calendar_version=calendar.version,
                          timezone="America/New_York", benchmark=benchmark)
    feed = "history/daily/unadjusted"
    manifest = freeze_market_frame(panel, root / "imports", vendor="kibot_public_guest",
        query_params={"feed": feed, "symbols": SYMBOLS, "from": labels[0], "to": labels[-1], "benchmark_id": benchmark},
        source_metadata={"documentation": {"prices": DOC, "access": ACCESS}, "source_responses": receipts,
                         "corporate_action_sources": action_receipts, "adjustment_comparison": diagnostics,
                         "security_mapping": "Provider ticker for this retrospective fixed cohort and window",
                         "cohort_security_ids": [f"kibot:{symbol}" for symbol in SYMBOLS], "calendar_version": calendar.version,
                         "usage": "guest access for internal evaluation; no independent entitlement review",
                         "availability": "per-session publication and revision times unavailable"})
    release = hashlib.sha256(json.dumps([r["sha256"] for r in receipts]).encode("ascii")).hexdigest()
    action_release = hashlib.sha256(json.dumps([r["sha256"] for r in action_receipts]).encode("ascii")).hexdigest()
    fields = ["security_id", "session", "open", "high", "low", "close", "volume"]
    bundle = {"schema_version": "external_market_evidence/v1", "manifest_id": manifest["snapshot_id"],
              "content_sha256": manifest["content_hash"].removeprefix("sha256:"), "scope": scope.model_dump(mode="json"),
              "coverage_start": labels[0], "coverage_end": labels[-1],
              "prices": {"adjustment": "raw", "currency": "USD", "rows": cast(pd.DataFrame, panel[fields]).to_dict(orient="records"),
                         "provenance": {"provider": "kibot_public_guest", "dataset": feed, "release": f"local_capture/{release}",
                                        "source_ref": DOC, "access_documentation": ACCESS, "source_responses": receipts}},
              "corporate_actions": {"events": events, "coverage": [{"security_id": f"kibot:{s}", "start": labels[0],
                   "end": labels[-1], "event_types": ["split", "cash_dividend"]} for s in eodhd.SYMBOLS],
                   "provenance": {"provider": eodhd.PROVIDER, "dataset": "eodhd/div+splits", "source_ref": eodhd.DOCS["actions"],
                                  "release": f"local_response_capture/sha256/{action_release}",
                                  "source_responses": action_receipts}}}
    eodhd.store_evidence(root, bundle, labels, benchmark_source_root)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true", help="Read missing raw/adjusted files using Kibot's public guest account.")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--action-root", type=Path, default=Path("data/us_public_eodhd_2025"))
    parser.add_argument("--benchmark", default="NASDAQXNDX", help="Predeclared benchmark ID; its evidence is supplied separately.")
    parser.add_argument("--benchmark-source-root", type=Path, help="Attach the independently captured NASDAQXNDX benchmark.")
    args = parser.parse_args()
    manifest = freeze_sample(args.root, args.action_root, fetch=args.fetch, benchmark=args.benchmark, benchmark_source_root=args.benchmark_source_root)
    print(json.dumps({"manifest_id": manifest["snapshot_id"], "rows": manifest["row_count"], "securities": len(SYMBOLS),
                      "sessions": len(manifest["source_metadata"]["input_attributes"]["calendar_sessions"]),
                      "import_root": str((args.root / "imports").resolve()), "evidence_root": str((args.root / "evidence").resolve())}))
