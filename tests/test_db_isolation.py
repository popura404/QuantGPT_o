"""Transaction isolation for the database engine used by application sessions."""

import asyncio

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from quantgpt import db


@pytest_asyncio.fixture(autouse=True)
async def _wake_thread_callbacks():
    # The sandbox may delay aiosqlite thread wakeups until the next loop timer.
    # Test ordering below uses Events, never elapsed sleeps.
    async def tick():
        while True:
            await asyncio.sleep(0.01)

    ticker = asyncio.create_task(tick())
    try:
        yield
    finally:
        ticker.cancel()
        await asyncio.gather(ticker, return_exceptions=True)


@pytest_asyncio.fixture
async def configured_engine(monkeypatch):
    monkeypatch.setattr(db, "_engine", None)
    monkeypatch.setattr(db, "_session_factory", None)

    def configure(url):
        if url is None:
            monkeypatch.delenv("DATABASE_URL", raising=False)
        else:
            monkeypatch.setenv("DATABASE_URL", url)
        return db._get_engine()

    try:
        yield configure
    finally:
        await db.close_db()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reader_finish", "writer_finish", "expected_rows"),
    [("rollback", "commit", 1), ("commit", "rollback", 0)],
)
async def test_file_sessions_isolate_uncommitted_writes(
    configured_engine, tmp_path, reader_finish, writer_finish, expected_rows,
):
    engine = configured_engine(f"sqlite+aiosqlite:///{tmp_path / 'isolated.sqlite'}")
    async with engine.begin() as connection:
        await connection.execute(text("CREATE TABLE probe (id INTEGER PRIMARY KEY)"))

    factory = async_sessionmaker(engine)
    write_started = asyncio.Event()
    reader_finished = asyncio.Event()

    async def writer():
        async with factory() as session:
            await session.execute(text("INSERT INTO probe (id) VALUES (1)"))
            write_started.set()
            await reader_finished.wait()
            await getattr(session, writer_finish)()

    async def reader():
        await write_started.wait()
        try:
            async with factory() as session:
                assert await session.scalar(text("SELECT count(*) FROM probe")) == 0
                await getattr(session, reader_finish)()
        finally:
            reader_finished.set()

    # The timeout only bounds failures; the Events determine transaction order.
    outcomes = await asyncio.wait_for(asyncio.gather(writer(), reader(), return_exceptions=True), timeout=10)
    for outcome in outcomes:
        if isinstance(outcome, BaseException):
            raise outcome
    async with factory() as session:
        assert await session.scalar(text("SELECT count(*) FROM probe")) == expected_rows


@pytest.mark.asyncio
async def test_file_connections_enable_wal_and_bounded_lock_wait(configured_engine, tmp_path):
    engine = configured_engine(f"sqlite+aiosqlite:///{tmp_path / 'configured.sqlite'}")
    # Hold both checkouts so the settings are checked on separate connections.
    async with engine.connect() as first, engine.connect() as second:
        for connection in (first, second):
            assert await connection.scalar(text("PRAGMA journal_mode")) == "wal"
            assert await connection.scalar(text("PRAGMA busy_timeout")) == 30_000


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [None, ""])
async def test_default_database_uses_file_connection_settings(configured_engine, monkeypatch, tmp_path, url):
    monkeypatch.chdir(tmp_path)
    engine = configured_engine(url)
    async with engine.connect() as connection:
        assert await connection.scalar(text("PRAGMA journal_mode")) == "wal"
    assert (tmp_path / "quantgpt.db").is_file()


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    "sqlite+aiosqlite://",
    "sqlite+aiosqlite:///:memory:",
    "sqlite+aiosqlite:///file:quantgpt_test?mode=memory&cache=shared&uri=true",
    "sqlite+aiosqlite:///file::memory:?cache=shared&uri=true",
])
async def test_memory_database_survives_connection_return(configured_engine, url):
    engine = configured_engine(url)
    async with engine.begin() as connection:
        await connection.execute(text("CREATE TABLE probe (id INTEGER PRIMARY KEY)"))
        await connection.execute(text("INSERT INTO probe (id) VALUES (1)"))
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM probe")) == 1
        assert await connection.scalar(text("PRAGMA journal_mode")) == "memory"
