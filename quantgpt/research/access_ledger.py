"""Authenticated MCP compatibility access to legacy experiment records."""

from sqlalchemy import and_, or_, select

from quantgpt import experiment_ledger as ledger
from quantgpt.models import Experiment, ProjectMember, User
from quantgpt.research.projects import MCP_SYSTEM_ACTOR, resolve_mcp_actor


def _scope():
    actor = resolve_mcp_actor()
    legacy = and_(Experiment.project_id.is_(None), Experiment.user_id == actor)
    if actor == MCP_SYSTEM_ACTOR:
        legacy = and_(Experiment.project_id.is_(None), or_(Experiment.user_id == actor, Experiment.user_id.is_(None)))
    active = select(User.id).where(User.id == actor, User.is_active.is_(True)).exists()
    return and_(active, or_(legacy, Experiment.project_id.in_(select(ProjectMember.project_id).where(
        ProjectMember.user_id == actor))))


async def get_experiment(session, experiment_id):
    return (await session.scalars(select(Experiment).where(Experiment.experiment_id == experiment_id, _scope()))).one_or_none()


async def list_experiments(session, *, status=None, universe=None, factor_hash=None, limit=50):
    statement = select(Experiment).where(_scope()).order_by(Experiment.created_at.desc()).limit(max(1, min(limit, 200)))
    for name, value in (("status", status), ("universe", universe), ("factor_hash", factor_hash)):
        if value is not None:
            statement = statement.where(getattr(Experiment, name) == value)
    return list((await session.scalars(statement)).all())


async def transition_status(session, experiment_id, new_status, **kwargs):
    row = await get_experiment(session, experiment_id)
    if row is None:
        raise ledger.ExperimentLedgerError("Experiment not found or inaccessible")
    if row.project_id is not None:
        from quantgpt.research.projects import require_project_member
        await require_project_member(session, resolve_mcp_actor(), row.project_id, write=True)
    return await ledger.transition_status(session, experiment_id, new_status, **kwargs)
