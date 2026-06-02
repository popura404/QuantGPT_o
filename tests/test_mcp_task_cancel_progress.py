"""MCP cooperative cancellation and progress tests."""

import asyncio
import json
import time

import pandas as pd
import pytest

from quantgpt import mcp_server, mcp_task_helper
from quantgpt.market_data import MarketDataFetcher
from quantgpt.task_store import CancelledException, tasks


@pytest.mark.asyncio
async def test_mcp_task_status_cancel_and_completion_preserves_cancelled(monkeypatch):
    async def fake_persist(*args, **kwargs):
        return None

    tasks.clear()
    monkeypatch.setattr(mcp_task_helper, "persist_task_to_db_async", fake_persist)

    task_id = await mcp_task_helper.start_mcp_task("score", "close", {"universe": "small_scale"})
    await mcp_task_helper.update_mcp_task_progress(
        task_id,
        status="fetching_data",
        progress=25,
        progress_message="fetching",
        progress_current=1,
        progress_total=4,
        stage="fetching_data",
    )

    status = mcp_task_helper.get_mcp_task_status_payload(task_id)
    assert status["status"] == "fetching_data"
    assert status["progress"] == 25
    assert status["progress_message"] == "fetching"

    cancelled = await mcp_task_helper.request_mcp_task_cancel(task_id)
    assert cancelled["status"] == "cancelled"
    assert cancelled["cancelled"] is True

    await mcp_task_helper.complete_mcp_task(
        task_id,
        {"error_code": "MCP_TASK_CANCELLED", "task_id": task_id},
        "cancelled",
        "close",
    )
    final_status = mcp_task_helper.get_mcp_task_status_payload(task_id, include_result=True)
    assert final_status["status"] == "cancelled"
    assert final_status["result"]["error_code"] == "MCP_TASK_CANCELLED"


@pytest.mark.asyncio
async def test_forced_mcp_task_id_reuses_existing_task(monkeypatch):
    async def fake_persist(*args, **kwargs):
        return None

    tasks.clear()
    monkeypatch.setattr(mcp_task_helper, "persist_task_to_db_async", fake_persist)

    task_id = await mcp_task_helper.start_mcp_task("queued", "close", {"source": "test"})
    token = mcp_task_helper.force_mcp_task_id(task_id)
    try:
        reused_id = await mcp_task_helper.start_mcp_task("score", "rank(close)", {"universe": "small_scale"})
    finally:
        mcp_task_helper.reset_forced_mcp_task_id(token)

    assert reused_id == task_id
    assert list(tasks) == [task_id]
    assert tasks[task_id]["task_type"] == "score"
    assert tasks[task_id]["expression"] == "rank(close)"


def test_fetch_stocks_checks_cancel_between_cache_reads(monkeypatch, tmp_path):
    fetcher = MarketDataFetcher(cache_dir=str(tmp_path))
    dates = pd.to_datetime(["2024-01-02", "2024-01-03"])

    def fake_load_cache(code):
        return pd.DataFrame({
            "trade_date": dates,
            "stock_code": [code, code],
            "open": [1.0, 1.0],
            "high": [1.0, 1.0],
            "low": [1.0, 1.0],
            "close": [1.0, 1.0],
            "volume": [100, 100],
            "amount": [100.0, 100.0],
            "pct_change": [0.0, 0.0],
        })

    calls = {"cancel": 0}
    progress = []

    def cancel_check():
        calls["cancel"] += 1
        if calls["cancel"] >= 2:
            raise CancelledException()

    monkeypatch.setattr(fetcher, "_load_cache", fake_load_cache)

    with pytest.raises(CancelledException):
        fetcher.fetch_stocks(
            ["sh.600000", "sh.600001"],
            "2024-01-02",
            "2024-01-03",
            cache_only=True,
            cancel_check=cancel_check,
            progress_callback=lambda done, total, message: progress.append((done, total, message)),
        )

    assert calls["cancel"] == 2
    assert progress == [(1, 2, "checked local cache for sh.600000")]


@pytest.mark.asyncio
async def test_compute_factor_values_submit_only_returns_task_and_background_completes(monkeypatch):
    completed = {}
    complete_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    async def fake_start(*args, **kwargs):
        return "task-submit"

    async def fake_complete(task_id, result=None, error=None, expression=None):
        completed["task_id"] = task_id
        completed["result"] = result
        completed["error"] = error
        loop.call_soon_threadsafe(complete_event.set)

    async def fake_progress(*args, **kwargs):
        return None

    def fake_payload(*args, **kwargs):
        time.sleep(0.01)
        return {"expression": args[0], "trading_days": 0, "data": []}

    monkeypatch.setattr(mcp_server, "start_mcp_task", fake_start)
    monkeypatch.setattr(mcp_server, "complete_mcp_task", fake_complete)
    monkeypatch.setattr(mcp_server, "update_mcp_task_progress", fake_progress)
    monkeypatch.setattr(mcp_server, "_compute_factor_values_payload", fake_payload)

    submitted = json.loads(await mcp_server.compute_factor_values("close", submit_only=True))

    assert submitted["submitted"] is True
    assert submitted["task_id"] == "task-submit"
    await asyncio.wait_for(complete_event.wait(), timeout=2)
    assert completed["task_id"] == "task-submit"
    assert completed["result"]["expression"] == "close"
    assert completed["error"] is None
