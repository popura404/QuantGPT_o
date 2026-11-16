"""Read or freeze official Nasdaq-100 gross total-return levels published by FRED."""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlencode

from quantgpt.us_data.calendar import NewYorkCalendar

BENCHMARK_ID = "NASDAQXNDX"
SERIES = f"https://fred.stlouisfed.org/series/{BENCHMARK_ID}"
METHODOLOGY = "https://indexes.nasdaq.com/docs/Index_Versions_NDX.pdf"


def build_benchmark(source_root: Path, sessions: list[str], *, fetch: bool = False) -> dict:
    """Build the benchmark evidence from dated consecutive levels, including the first day's previous close."""
    from scripts.freeze_us_public_sample import capture_response

    if not sessions:
        raise ValueError("No study sessions for the benchmark")
    start, end = date.fromisoformat(sessions[0]), date.fromisoformat(sessions[-1])
    calendar = NewYorkCalendar()
    expected = [row.session.isoformat() for row in calendar.sessions(start, end)]
    if sessions != expected:
        raise ValueError("Benchmark study sessions must match the XNYS calendar")
    previous = calendar.sessions(start - timedelta(days=14), start - timedelta(days=1))[-1].session.isoformat()
    window = f"{previous}_{end.isoformat()}"
    params = urlencode({"id": BENCHMARK_ID, "cosd": previous, "coed": end.isoformat()})
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?{params}"
    raw, receipt = capture_response(source_root, BENCHMARK_ID, window, url, "csv", fetch=fetch,
        validator=lambda content: content.decode("utf-8-sig").splitlines()[0] == f"observation_date,{BENCHMARK_ID}")
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    if reader.fieldnames != ["observation_date", BENCHMARK_ID]:
        raise ValueError("FRED returned a different series or CSV schema")
    levels = {}
    seen = set()
    for row in reader:
        day = date.fromisoformat(row["observation_date"]).isoformat()
        if day in seen:
            raise ValueError(f"Duplicate FRED benchmark observation on {day}")
        seen.add(day)
        if row[BENCHMARK_ID] in {"", "."}:
            continue
        value = float(row[BENCHMARK_ID])
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"Invalid FRED benchmark level on {day}")
        levels[day] = value
    if set(levels) != {previous, *sessions}:
        raise ValueError("FRED levels differ from the study sessions and the required previous close")
    observations = [{"session": day, "previous_total_return_level": levels[prior],
                     "total_return_level": levels[day], "return": levels[day] / levels[prior] - 1}
                    for prior, day in zip([previous, *sessions[:-1]], sessions, strict=True)]
    return {"benchmark_id": BENCHMARK_ID, "currency": "USD", "return_type": "total_return",
            "reinvestment_policy": "gross_cash_dividends_reinvested_on_ex_date",
            "provenance": {"provider": "Nasdaq, Inc. via Federal Reserve Bank of St. Louis (FRED)",
                           "dataset": BENCHMARK_ID, "source_ref": SERIES, "release": f"sha256:{receipt['sha256']}",
                           "data_url": url, "index_symbol": "XNDX", "methodology_url": METHODOLOGY,
                           "rights_status": "FRED marks this Nasdaq series copyrighted; pre-approval required."
                                           " No redistribution license is provided.",
                           "receipt": receipt},
            "observations": observations}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="Local directory for the original FRED response and receipt")
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--fetch", action="store_true", help="Retrieve the missing original response from FRED")
    args = parser.parse_args()
    sessions = [row.session.isoformat() for row in NewYorkCalendar().sessions(args.start, args.end)]
    bench = build_benchmark(args.root, sessions, fetch=args.fetch)
    print(json.dumps({"benchmark_id": BENCHMARK_ID, "coverage_start": sessions[0], "coverage_end": sessions[-1],
                      "sessions": len(sessions), "provenance": bench["provenance"]}, indent=2))


if __name__ == "__main__":
    main()
