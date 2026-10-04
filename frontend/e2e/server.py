"""Real HTTP services + isolated SQLite for browser tests; no external schedulers."""
import asyncio
import json
import os
import secrets
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
RUN_ROOT = ROOT / "test-results" / "browser" / uuid.uuid4().hex
RUN_ROOT.mkdir(parents=True)
os.environ.update({
    "DATABASE_URL": "sqlite+aiosqlite:///" + (RUN_ROOT / "research.db").as_posix(),
    "JWT_SECRET_KEY": secrets.token_hex(32),
    "QUANTGPT_ADMIN_PASSWORD": secrets.token_hex(20),
    "AUTH_DISABLED": "false",
    "QUANTGPT_RESEARCH_SNAPSHOT_ROOT": str(RUN_ROOT / "snapshots"),
})

from quantgpt.api_server import app  # noqa: E402
from quantgpt.auth import hash_password  # noqa: E402
from quantgpt.db import _get_session_factory, close_db, init_db  # noqa: E402
from quantgpt.models import User  # noqa: E402
from quantgpt.research.projects import MCP_ACTOR, create_project  # noqa: E402

OWNER_ID = uuid.UUID("d6a1c2bd-099c-400e-823a-aeb9a2a1fcb1")


@asynccontextmanager
async def browser_lifespan(_app):
    from quantgpt import task_store
    from quantgpt.mcp_server import save_factor_pool_entry

    task_store.main_loop = asyncio.get_running_loop()
    await init_db()
    async with _get_session_factory()() as session:
        session.add_all([
            User(id=OWNER_ID, email="research@example.test", nickname="Browser Researcher",
                 password_hash=hash_password("BrowserFixture-Only-2026"), subscribe_weekly=False),
            User(id=uuid.UUID("82f85472-1e2b-4d74-90fd-241a2a2ef685"), email="outsider@example.test",
                 password_hash=hash_password("BrowserFixture-Only-2026"), subscribe_weekly=False),
        ])
        await session.flush()
        project = await create_project(session, OWNER_ID, name="MCP 共享测试项目", market="demo_global_equity")
        await session.commit()
        project_id = str(project.id)
    token = MCP_ACTOR.set(OWNER_ID)
    try:
        result = json.loads(await save_factor_pool_entry(
            expression="rank(close)", name="MCP 写入的固定样例", market="demo_global_equity",
            project_id=project_id, tags=["browser_fixture"],
        ))
        if "entry" not in result:
            raise RuntimeError(f"MCP fixture setup failed: {result}")
    finally:
        MCP_ACTOR.reset(token)
    yield
    await close_db()


app.router.lifespan_context = browser_lifespan

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8017, log_level="warning")
