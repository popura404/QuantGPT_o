"""Database engine, session factory, and FastAPI dependency."""

import logging
import os

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .models import Base

logger = logging.getLogger(__name__)

_engine = None
_session_factory = None


def _get_engine():
    global _engine
    if _engine is None:
        url = os.environ.get("DATABASE_URL") or "sqlite+aiosqlite:///./quantgpt.db"
        kwargs: dict = {"echo": False}
        if "postgresql" in url:
            kwargs["pool_size"] = 5
            kwargs["max_overflow"] = 10
        elif "sqlite" in url:
            from sqlalchemy.pool import StaticPool
            kwargs["connect_args"] = {"check_same_thread": False}
            kwargs["poolclass"] = StaticPool
        _engine = create_async_engine(url, **kwargs)
    return _engine


def _get_session_factory():
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            _get_engine(), class_=AsyncSession, expire_on_commit=False
        )
    return _session_factory


async def get_db():
    """FastAPI dependency that yields an async DB session."""
    factory = _get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db():
    """Create all tables (dev convenience). Use Alembic for production."""
    engine = _get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(_check_research_schema)
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate_add_columns)
    logger.info("Database tables created/verified")


def _check_research_schema(connection):
    """Never leave a pre-upgrade database with a partially created new schema."""
    import sqlalchemy as sa

    inspector = sa.inspect(connection)
    requirements = {"experiments": {"project_id", "evaluation_hash", "evaluation_config"},
                    "factor_pool_entries": {"project_id", "evaluation_id"},
                    "strategies": {"project_id"}, "tasks": {"attempt_id", "dispatch_pending", "revision"}}
    missing = []
    for table, columns in requirements.items():
        if inspector.has_table(table):
            present = {column["name"] for column in inspector.get_columns(table)}
            missing.extend(f"{table}.{column}" for column in sorted(columns - present))
    if missing:
        raise RuntimeError("DATABASE_MIGRATION_REQUIRED: back up the database and run Alembic upgrade head; "
                           "see docs/RESEARCH_MIGRATION.md. Missing: " + ", ".join(missing))


def _migrate_add_columns(connection):
    """Add columns that were added after initial table creation."""
    import sqlalchemy as sa
    inspector = sa.inspect(connection)
    _add_column_if_missing(
        connection, inspector, "submitted_alphas", "tag",
        "ALTER TABLE submitted_alphas ADD COLUMN tag VARCHAR(100)",
    )


def _add_column_if_missing(connection, inspector, table, column, ddl):
    import sqlalchemy as sa
    if inspector.has_table(table):
        cols = [c["name"] for c in inspector.get_columns(table)]
        if column not in cols:
            connection.execute(sa.text(ddl))
            logger.info(f"Migration: added column {table}.{column}")


async def close_db():
    """Close the engine connection pool."""
    global _engine, _session_factory
    if _engine:
        await _engine.dispose()
        _engine = None
        _session_factory = None
