"""Exercise actual Alembic upgrades and safe rollback against SQLite."""

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


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
                spec = importlib.util.spec_from_file_location(f"migration_{path.stem}", path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
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
