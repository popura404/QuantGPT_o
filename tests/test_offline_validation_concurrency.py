"""Independent validators publish one immutable report to file-backed SQLite."""

import asyncio
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from quantgpt import db
from quantgpt.models import Base, ExperimentArtifact, ResearchAuditEvent, User
from quantgpt.research.contracts import EvaluationConfigV1, FactorDefinitionV1
from quantgpt.research.demo import prepare_offline_demo
from quantgpt.research.evaluations import evaluate_factor_batch, read_artifact
from quantgpt.research.projects import create_project
from quantgpt.research.validation_runner import run_local_validation


@pytest.mark.asyncio
async def test_concurrent_validators_publish_one_verified_report(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_engine", None)
    monkeypatch.setattr(db, "_session_factory", None)
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'validation.sqlite'}")
    engine = db._get_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    root = tmp_path / "snapshots"
    actor_id = uuid.uuid4()
    ready = asyncio.Event()
    missing_artifact_ids = []
    publication_connections = []

    class ConcurrentSession(AsyncSession):
        async def get(self, entity, ident, **kwargs):
            value = await super().get(entity, ident, **kwargs)
            if entity is ExperimentArtifact and value is None and not self.info.get("publication_barrier_passed"):
                # Both publishers must observe the same absent artifact before
                # either can insert. The timeout only bounds a broken barrier.
                connection = await self.connection()
                publication_connections.append(connection.sync_connection.connection.driver_connection)
                missing_artifact_ids.append(ident)
                self.info["publication_barrier_passed"] = True
                if len(missing_artifact_ids) == 2:
                    ready.set()
                await asyncio.wait_for(ready.wait(), timeout=30)
            return value

    async def validate():
        async with ConcurrentSession(engine, expire_on_commit=False) as session:
            return await run_local_validation(session, actor_id, project_id, evaluation_id, snapshot_root=root)

    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        demo = await asyncio.to_thread(prepare_offline_demo, root)
        async with factory() as session:
            session.add(User(id=actor_id, email="concurrent-validation@example.com", is_active=True))
            await session.commit()
            project = await create_project(session, actor_id, name="Concurrent report publication")
            project_id = project.id
            session.add(ResearchAuditEvent(project_id=project_id, actor_id=actor_id,
                action="snapshot_registered", payload={"snapshot_id": demo["config"]["data_inputs"][0]["manifest_id"],
                "source": "synthetic_offline_fixture"}))
            await session.commit()
            evaluated = await evaluate_factor_batch(session, actor_id, project_id,
                [FactorDefinitionV1.model_validate(item) for item in demo["definitions"]],
                EvaluationConfigV1.model_validate(demo["config"]), snapshot_root=root)
            evaluation = evaluated["evaluations"][0]
            assert evaluation["summary"]["performance_observed"], evaluation
            evaluation_id = evaluation["evaluation_id"]

        # Actual snapshot loads, frozen-engine replays, and artifact writes run
        # concurrently; no numerical output or persistence result is mocked.
        reports = await asyncio.wait_for(asyncio.gather(validate(), validate(), return_exceptions=True), timeout=120)
        assert len(missing_artifact_ids) == 2
        assert missing_artifact_ids[0] == missing_artifact_ids[1]
        assert publication_connections[0] is not publication_connections[1]
        for report in reports:
            if isinstance(report, BaseException):
                raise report
        first_report, second_report = reports
        assert isinstance(first_report, dict)
        assert first_report == second_report
        assert first_report["status"] == "completed"
        assert first_report["checks"]["simulation"]["status"] == "passed"

        async with factory() as session:
            ids = list(await session.scalars(select(ExperimentArtifact.id).where(
                ExperimentArtifact.experiment_id == evaluation_id,
                ExperimentArtifact.artifact_type == "offline_validation")))
            assert ids == [missing_artifact_ids[0]]
            saved = await read_artifact(session, actor_id, project_id, ids[0])
            assert saved["payload"] == first_report
    finally:
        await db.close_db()
