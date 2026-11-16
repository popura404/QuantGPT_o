"""Authenticated MCP compatibility access to legacy experiment records."""

import uuid

from sqlalchemy import and_, or_, select
from sqlalchemy.sql.elements import ColumnElement

from quantgpt import experiment_ledger as ledger
from quantgpt.models import Experiment, ProjectMember, User
from quantgpt.research.projects import MCP_SYSTEM_ACTOR, require_project_member, resolve_mcp_actor


def experiment_scope(actor_id: uuid.UUID, *, write: bool = False) -> ColumnElement[bool]:
    legacy = and_(Experiment.project_id.is_(None), Experiment.user_id == actor_id)
    if actor_id == MCP_SYSTEM_ACTOR:
        legacy = and_(Experiment.project_id.is_(None), or_(Experiment.user_id == actor_id, Experiment.user_id.is_(None)))
    active = select(User.id).where(User.id == actor_id, User.is_active.is_(True)).exists()
    projects = select(ProjectMember.project_id).where(ProjectMember.user_id == actor_id)
    if write:
        projects = projects.where(ProjectMember.role.in_(("owner", "admin", "researcher")))
    return and_(active, or_(legacy, Experiment.project_id.in_(projects)))


async def get_experiment(session, experiment_id, *, write: bool = False):
    stmt = select(Experiment).where(Experiment.experiment_id == experiment_id,
                                    experiment_scope(resolve_mcp_actor(), write=write))
    return (await session.scalars(stmt.with_for_update() if write else stmt)).one_or_none()


async def list_experiments(session, *, status=None, universe=None, factor_hash=None, limit=50):
    statement = select(Experiment).where(experiment_scope(resolve_mcp_actor())).order_by(
        Experiment.created_at.desc()).limit(max(1, min(limit, 200)))
    for name, value in (("status", status), ("universe", universe), ("factor_hash", factor_hash)):
        if value is not None:
            statement = statement.where(getattr(Experiment, name) == value)
    return list((await session.scalars(statement)).all())


async def transition_status(session, experiment_id, new_status, **kwargs):
    row = await get_experiment(session, experiment_id, write=True)
    if row is None:
        raise ledger.ExperimentLedgerError("Experiment not found or inaccessible")
    return await ledger.transition_status(session, experiment_id, new_status, **kwargs)


async def summarize_trial_counts(session, *, project_id: uuid.UUID | None = None, universe=None, factor_hash=None):
    actor = resolve_mcp_actor()
    if project_id is not None:
        await require_project_member(session, actor, project_id)
    counts = await ledger.summarize_trial_counts(session, user_id=None if project_id else actor,
        universe=universe, factor_hash=factor_hash, visibility=experiment_scope(actor), project_id=project_id)
    return {**counts, "scope": "project" if project_id else "actor_visible_authored",
            "project_id": str(project_id) if project_id else None}
