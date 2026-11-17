"""Dispatcher ownership under overlapping and disconnected MCP clients."""

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from quantgpt import db, mcp_server, task_executor


@pytest.fixture
def simulated_dispatcher(monkeypatch):
    @asynccontextmanager
    async def session():
        yield AsyncMock()

    monkeypatch.setattr(db, "init_db", AsyncMock())
    monkeypatch.setattr(db, "_get_session_factory", lambda: session)
    monkeypatch.setattr(mcp_server, "ensure_mcp_system_user", AsyncMock())
    monkeypatch.setattr(task_executor, "dispatch_pending_tasks_once", AsyncMock(return_value=0))
    monkeypatch.setattr(task_executor, "_dispatcher_task", None)
    monkeypatch.setattr(task_executor, "_dispatcher_owners", 0)
    monkeypatch.setattr(task_executor, "_worker_monitors", set())


@pytest.mark.asyncio
@pytest.mark.parametrize("http_owner", [False, True])
async def test_disconnected_agent_leaves_other_dispatcher_owners_running(simulated_dispatcher, http_owner):
    ready = asyncio.Event()

    async def agent():
        async with mcp_server._research_mcp_lifespan(None):
            ready.set()
            await asyncio.Event().wait()

    async with AsyncExitStack() as app:
        if http_owner:
            await app.enter_async_context(task_executor.task_dispatcher_lifespan())
        disconnected = asyncio.create_task(agent())
        await ready.wait()
        dispatcher = task_executor._dispatcher_task
        async with mcp_server._research_mcp_lifespan(None):
            monitor = asyncio.create_task(asyncio.Event().wait())
            task_executor._worker_monitors.add(monitor)
            task_executor.start_task_dispatcher()
            disconnected.cancel()
            with pytest.raises(asyncio.CancelledError):
                await disconnected
            assert task_executor._dispatcher_task is dispatcher
            assert not dispatcher.done() and not monitor.done()
        if http_owner:
            assert task_executor._dispatcher_task is dispatcher
            assert not monitor.done()
    assert dispatcher.cancelled() and monitor.cancelled()
    assert task_executor._dispatcher_task is None


@pytest.mark.asyncio
async def test_new_session_during_last_disconnect_keeps_its_new_dispatcher(simulated_dispatcher, monkeypatch):
    started, draining, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def work():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            draining.set()
            await release.wait()

    monkeypatch.setattr(task_executor, "dispatch_pending_tasks_once", work)
    async with AsyncExitStack() as old_session:
        await old_session.enter_async_context(mcp_server._research_mcp_lifespan(None))
        await started.wait()
        old_dispatcher = task_executor._dispatcher_task
        closing = asyncio.create_task(old_session.aclose())
        await draining.wait()
        try:
            async with mcp_server._research_mcp_lifespan(None):
                new_dispatcher = task_executor._dispatcher_task
                new_monitor = asyncio.create_task(asyncio.Event().wait())
                task_executor._worker_monitors.add(new_monitor)
                release.set()
                await closing
                assert old_dispatcher.cancelled()
                assert new_dispatcher is not old_dispatcher and not new_dispatcher.done()
                assert task_executor._dispatcher_task is new_dispatcher
                assert new_monitor in task_executor._worker_monitors
        finally:
            release.set()
            await closing
    assert new_dispatcher.cancelled() and new_monitor.cancelled()
