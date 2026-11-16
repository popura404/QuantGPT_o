"""Exercise actual Alembic upgrades and safe rollback against SQLite."""

import importlib.util
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def _version(path):
    spec = importlib.util.spec_from_file_location(f"migration_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_upgrade_roundtrip_and_preserve_new_writes(tmp_path):
    root = Path(__file__).resolve().parents[1] / "quantgpt" / "migrations" / "versions"
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'migration.sqlite'}")
    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            latest = None
            for path in sorted(root.glob("*.py")):
                if int(path.name[:3]) > 17:
                    continue
                module = _version(path)
                module.upgrade()
                latest = module
            assert "project_id" in {col["name"] for col in sa.inspect(connection).get_columns("experiments")}
            latest.downgrade()
            assert "project_id" not in {col["name"] for col in sa.inspect(connection).get_columns("experiments")}
            latest.upgrade()
            connection.execute(sa.text("INSERT INTO research_projects (id,owner_user_id,name,market,config,revision,created_at) "
                "VALUES (:id,:owner,'post upgrade','us_equity','{}',0,CURRENT_TIMESTAMP)"),
                {"id": "a" * 32, "owner": "00000000000000000000000000000002"})
            with pytest.raises(RuntimeError, match="retain expanded schema"):
                latest.downgrade()
            assert connection.scalar(sa.text("SELECT COUNT(*) FROM research_projects")) == 1
    engine.dispose()


def test_round_lease_migration_preserves_017_rows_and_blocks_nonempty_downgrade(tmp_path):
    from quantgpt.db import _check_research_schema
    from quantgpt.models import (
        EvaluationExecutionLease,
        Experiment,
        ResearchDirection,
        ResearchProject,
        ResearchRound,
        User,
    )

    root = Path(__file__).resolve().parents[1] / "quantgpt" / "migrations" / "versions"
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'rounds.sqlite'}")
    actor, project, round_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
        for path in sorted(root.glob("[0-9]*.py")):
            if int(path.name[:3]) < 18:
                _version(path).upgrade()
        connection.execute(User.__table__.insert().values(id=actor, email="legacy@example.invalid"))
        connection.execute(ResearchProject.__table__.insert().values(id=project, owner_user_id=actor, name="Preserved"))
        connection.execute(Experiment.__table__.insert().values(experiment_id="ev_legacy", expression="close",
                           expression_normalized="close", factor_hash="fixed"))
        connection.execute(sa.text("CREATE TABLE alembic_version(version_num VARCHAR(32) PRIMARY KEY)"))
        connection.execute(sa.text("INSERT INTO alembic_version VALUES ('017')"))
        with pytest.raises(RuntimeError, match="DATABASE_MIGRATION_REQUIRED"):
            _check_research_schema(connection)
        assert "research_rounds" not in sa.inspect(connection).get_table_names()
        upgrade = _version(next(root.glob("018_*.py")))
        assert upgrade.down_revision == "017"
        upgrade.upgrade()
        connection.execute(sa.text("UPDATE alembic_version SET version_num = '018'"))
        _check_research_schema(connection)
        for model in (ResearchRound, ResearchDirection, EvaluationExecutionLease):
            inspector = sa.inspect(connection)
            table = model.__table__
            assert {c["name"]: c["nullable"] for c in inspector.get_columns(table.name)} == {
                col.name: col.nullable for col in table.columns}
            assert inspector.get_pk_constraint(table.name)["constrained_columns"] == [c.name for c in table.primary_key]
            assert {i["name"] for i in inspector.get_indexes(table.name)} == {i.name for i in table.indexes}
            assert {tuple(c["column_names"]) for c in inspector.get_unique_constraints(table.name)} == {
                tuple(col.name for col in constraint.columns) for constraint in table.constraints
                if isinstance(constraint, sa.UniqueConstraint)}
            assert {(k["constrained_columns"][0], k["referred_table"], k["options"].get("ondelete"))
                    for k in inspector.get_foreign_keys(table.name)} == {
                (key.parent.name, key.column.table.name, key.ondelete) for key in table.foreign_keys}
        upgrade.downgrade()
        assert "research_rounds" not in sa.inspect(connection).get_table_names()
        upgrade.upgrade()
        assert connection.scalar(sa.select(ResearchProject.name).where(ResearchProject.id == project)) == "Preserved"
        lease = EvaluationExecutionLease.__table__
        connection.execute(lease.insert().values(evaluation_id="ev_legacy", owner_token="owner",
                                                 expires_at=datetime.now(timezone.utc)))
        with pytest.raises(RuntimeError, match="retain expanded schema"):
            upgrade.downgrade()
        assert connection.scalar(sa.select(lease.c.owner_token)) == "owner"
        connection.execute(lease.delete().where(lease.c.evaluation_id == "ev_legacy"))
        connection.execute(ResearchRound.__table__.insert().values(id=round_id, project_id=project,
            coordinator_id=actor, coordinator_digest="digest", selection_config={}, holdout={}))
        with pytest.raises(RuntimeError, match="retain expanded schema"):
            upgrade.downgrade()
        assert connection.scalar(sa.select(ResearchRound.id).where(ResearchRound.project_id == project)) == round_id
    engine.dispose()
