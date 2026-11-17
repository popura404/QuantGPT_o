"""Prepare Alpaca SIP / SEC inputs for shared research, without submitting experiments.

Run from the repository root: python -m scripts.freeze_us_research --help
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import date
from pathlib import Path

from quantgpt.us_data.alpaca import AlpacaDailyProvider
from quantgpt.us_data.cache import CachedJSONClient
from quantgpt.us_data.contracts import DataCapabilityError
from quantgpt.us_data.fundamentals import DEFAULT_FIELDS
from quantgpt.us_data.preparation import prepare_us_snapshot
from quantgpt.us_data.sec import FIELD_CONTRACTS, SECCompanyFactsProvider

ENV_KEYS = {"APCA_API_KEY_ID", "APCA_API_SECRET_KEY", "SEC_USER_AGENT", "QUANTGPT_US_SECURITIES_FILE",
            "QUANTGPT_US_FUNDAMENTAL_FIELDS"}


def load_local_config(path: Path = Path(".env")) -> None:
    """Read only this command's configuration; existing environment values win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.strip().partition("=")
        if separator and key.strip() in ENV_KEYS:
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            os.environ.setdefault(key.strip(), value)


def main(argv: list[str] | None = None) -> int:
    load_local_config()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--securities", type=Path, default=os.environ.get("QUANTGPT_US_SECURITIES_FILE") or None)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--root", type=Path, default=Path("data/us_research"))
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--fundamental-fields", nargs="+", choices=tuple(FIELD_CONTRACTS))
    selection.add_argument("--price-only", action="store_true")
    parser.add_argument("--refresh", action="store_true", help="Fetch fresh provider responses; keep previous frozen snapshots")
    args = parser.parse_args(argv)
    if args.securities is None:
        parser.error("--securities or QUANTGPT_US_SECURITIES_FILE is required")
    configured = tuple(name.strip() for name in os.environ.get(
        "QUANTGPT_US_FUNDAMENTAL_FIELDS", ",".join(DEFAULT_FIELDS),
    ).split(",") if name.strip())
    fields = () if args.price_only else tuple(args.fundamental_fields or configured)
    try:
        http = CachedJSONClient(args.root / "responses", refresh=args.refresh)
        result = prepare_us_snapshot(args.securities, args.start, args.end, args.root,
                                     prices=AlpacaDailyProvider(http=http),
                                     fundamentals=SECCompanyFactsProvider(os.environ.get("SEC_USER_AGENT", ""), http=http)
                                     if fields else None, fields=fields)
    except DataCapabilityError as exc:
        print(json.dumps(exc.to_dict(), ensure_ascii=False))
        return 1
    except (ValueError, OSError, TypeError, KeyError):
        print(json.dumps({"error_code": "US_PREPARATION_INVALID", "message": "Check the cohort file, date range and output directory.",
                          "retryable": False, "next_action": "fix_input_configuration"}))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
