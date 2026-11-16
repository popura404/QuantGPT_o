import hashlib
import json

import httpx
import pandas as pd
import pytest

from scripts.freeze_kibot_us_sample import daily_bars
from scripts.freeze_us_public_sample import parse_symbol, source


def test_as_traded_volume_and_dividend_amount_use_the_share_units_on_the_date():
    dates = ["2024-03-01", "2024-03-04"]
    bars = [{"date": day, "open": close, "high": close, "low": close, "close": close, "volume": 1000}
            for day, close in zip(dates, (100, 50), strict=True)]
    splits = [{"date": dates[1], "split": "2.000000/1.000000"}]
    div = [{"date": dates[0], "paymentDate": "2024-03-08", "value": .25, "unadjustedValue": .50, "currency": "USD"}]
    prices, actions = parse_symbol("AAPL", bars, splits, div, set(dates))
    assert [row["volume"] for row in prices] == [500, 1000]
    assert [row["close"] for row in prices] == [100, 50]
    assert actions[0]["ratio"] == 2 and actions[1]["per_share"] == .50 and actions[1]["pay_session"] == "2024-03-08"
    with pytest.raises(ValueError, match="sessions"):
        parse_symbol("AAPL", bars[:1], splits, div, set(dates))


def test_offline_response_validates_its_receipt(tmp_path):
    directory = tmp_path / "sources"
    directory.mkdir()
    raw = b"[]"
    (directory / "AAPL.splits.json").write_bytes(raw)
    url = "https://eodhd.com/api/splits/AAPL.US?from=2023-01-03&fmt=json&api_token=demo"
    receipt = {"url": url, "sha256": hashlib.sha256(raw).hexdigest()}
    (directory / "AAPL.splits.receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
    assert source(tmp_path, "AAPL", "splits", fetch=False) == ([], receipt)
    (directory / "AAPL.splits.json").write_bytes(b"[1]")
    with pytest.raises(ValueError, match="receipt differs"):
        source(tmp_path, "AAPL", "splits", fetch=False)


def test_kibot_guest_errors_and_missing_exchange_rows_are_rejected(tmp_path):
    dates = pd.DatetimeIndex(["2024-01-03", "2024-01-04"], dtype="datetime64[ns]")
    rows = b"01/03/2024,99,102,98,101,10\r\n01/04/2024,101,103,99,103,12\r\n"
    guest_error = httpx.MockTransport(lambda req: httpx.Response(200, content=b"402 Unauthorized"))
    with httpx.Client(transport=guest_error) as denied, pytest.raises(ValueError, match="Unexpected .*response"):
        daily_bars(denied, tmp_path, "MSFT", dates, fetch=True, raw_prices=True)
    with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, content=rows))) as client:
        bars, receipt = daily_bars(client, tmp_path, "MSFT", dates, fetch=True, raw_prices=True)
        assert bars["volume"].tolist() == [10, 12]
        assert receipt["sha256"] == hashlib.sha256(rows).hexdigest()
        assert "unadjusted=1" in receipt["url"]
        with pytest.raises(ValueError, match="missing trading days"):
            daily_bars(client, tmp_path, "MSFT", dates.append(pd.DatetimeIndex(["2024-01-05"])), fetch=False, raw_prices=True)
