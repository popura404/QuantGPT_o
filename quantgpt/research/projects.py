"""Shared project authorization for HTTP, MCP and durable workers."""

from __future__ import annotations

import os
import uuid
from contextvars import ContextVar
from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from quantgpt.models import FactorPoolEntry, ProjectMember, ResearchAuditEvent, ResearchProject, SavedFactor, User

MCP_ACTOR: ContextVar[uuid.UUID | None] = ContextVar("mcp_actor", default=None)
MCP_SYSTEM_ACTOR = uuid.UUID("00000000-0000-0000-0000-000000000002")


class ProjectAccessError(ValueError):
    """Object is absent or inaccessible; callers must not distinguish these."""


def resolve_mcp_actor() -> uuid.UUID:
    """Identity comes from authenticated transport or server-owned stdio config."""
    current = MCP_ACTOR.get()
    if current is not None:
        return current
    return uuid.UUID(os.environ.get("QUANTGPT_MCP_STDIO_USER_ID") or str(MCP_SYSTEM_ACTOR))


async def require_project_member(session: AsyncSession, actor_id: str | uuid.UUID,
                                 project_id: str | uuid.UUID, *, write: bool = False) -> ProjectMember:
    try:
        actor, project = uuid.UUID(str(actor_id)), uuid.UUID(str(project_id))
    except (ValueError, TypeError) as exc:
        raise ProjectAccessError("Project not found or inaccessible") from exc
    result = await session.execute(select(ProjectMember).join(User, User.id == ProjectMember.user_id).where(
        ProjectMember.project_id == project, ProjectMember.user_id == actor, User.is_active.is_(True),
    ))
    member = result.scalar_one_or_none()
    if member is None or (write and member.role not in {"owner", "admin", "researcher"}):
        raise ProjectAccessError("Project not found or inaccessible")
    return member


async def create_project(session: AsyncSession, actor_id: uuid.UUID, *, name: str,
                         market: str = "us_equity", config: dict | None = None) -> ResearchProject:
    if not name.strip():
        raise ValueError("Project name must not be empty")
    user = (await session.execute(select(User).where(User.id == actor_id, User.is_active.is_(True)))).scalar_one_or_none()
    if user is None:
        raise ProjectAccessError("Active principal required")
    project = ResearchProject(id=uuid.uuid4(), owner_user_id=actor_id, name=name.strip(), market=market, config=config or {})
    session.add(project)
    await session.flush()
    session.add(ProjectMember(project_id=project.id, user_id=actor_id, role="owner"))
    session.add(ResearchAuditEvent(project_id=project.id, actor_id=actor_id, action="project_created", payload={}))
    await session.flush()
    return project


async def set_project_member(session: AsyncSession, actor_id: uuid.UUID, project_id: uuid.UUID,
                             user_id: uuid.UUID, role: str | None) -> None:
    member = await require_project_member(session, actor_id, project_id, write=True)
    if member.role not in {"owner", "admin"}:
        raise ProjectAccessError("Project administrator required")
    if role not in {None, "admin", "researcher", "viewer"}:
        raise ValueError("Invalid member role")
    target = await session.get(ProjectMember, (project_id, user_id))
    if target is not None and target.role == "owner":
        raise ProjectAccessError("Project ownership cannot be reassigned through member updates")
    if role is None:
        if target is not None:
            await session.delete(target)
    elif target is None:
        user = await session.get(User, user_id)
        if user is None:
            raise ProjectAccessError("Active principal required")
        session.add(ProjectMember(project_id=project_id, user_id=user_id, role=role))
    else:
        target.role = role
    session.add(ResearchAuditEvent(project_id=project_id, actor_id=actor_id, action="membership_changed",
                                  payload={"user_id": str(user_id), "role": role}))
    await session.flush()


async def list_projects(session: AsyncSession, actor_id: uuid.UUID) -> list[ResearchProject]:
    return list((await session.scalars(select(ResearchProject).join(ProjectMember).where(
        ProjectMember.user_id == actor_id).order_by(ResearchProject.created_at.desc()))).all())


async def migrate_personal_favorites(session: AsyncSession, actor_id: uuid.UUID, project_id: uuid.UUID,
                                     *, dry_run: bool = True) -> dict:
    """Explicit, idempotent adoption of only the authenticated user's favorites.

    Unknown system ownership is never assigned to a user's project. Original
    rows and payloads remain intact for compatibility and application rollback.
    """
    from quantgpt.experiment_ledger import compute_factor_hash
    from quantgpt.expression_parser import normalize_expression
    from quantgpt.factor_pool import normalize_tags

    await require_project_member(session, actor_id, project_id, write=True)
    saved = list((await session.scalars(select(SavedFactor).where(SavedFactor.user_id == actor_id))).all())
    existing = set((await session.scalars(select(FactorPoolEntry.legacy_saved_factor_id).where(
        FactorPoolEntry.legacy_saved_factor_id.is_not(None)))).all())
    pending = [item for item in saved if item.id not in existing]
    if not dry_run:
        for item in pending:
            tags, category = normalize_tags(cast(list[str] | None, item.tags))
            session.add(FactorPoolEntry(owner_user_id=actor_id, project_id=project_id, legacy_saved_factor_id=item.id,
                expression=item.expression, expression_normalized=normalize_expression(str(item.expression)),
                factor_hash=compute_factor_hash(str(item.expression), cast(dict | None, item.params)), name=item.name, note=item.note,
                tags=tags, category_tag=category, market=item.market, metrics=item.metrics,
                backtest_summary=item.backtest_summary, params=item.params, report_url=item.report_url,
                task_id=item.task_id, source="legacy_favorite", pool_status="watchlist",
                validation_provenance={"status": "legacy_unverified", "recompute_required": True},
                created_at=item.created_at, updated_at=item.updated_at))
        session.add(ResearchAuditEvent(project_id=project_id, actor_id=actor_id, action="personal_favorites_imported",
                                      payload={"count": len(pending), "source_ids": [str(item.id) for item in pending]}))
        await session.flush()
    return {"dry_run": dry_run, "original_count": len(saved), "already_linked": len(saved) - len(pending),
            "pending_count": len(pending), "source_ids": [str(item.id) for item in pending]}
