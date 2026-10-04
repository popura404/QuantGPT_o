"""Immutable pre-selection economic hypotheses and frozen baseline references."""

from __future__ import annotations

import re
import uuid
from datetime import timezone
from typing import Any, cast

from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from quantgpt.models import Experiment, ResearchAuditEvent, ResearchProject
from quantgpt.research.contracts import EvaluationRef, FactorDefinitionV1, content_hash, definition_hash
from quantgpt.research.projects import ProjectAccessError, require_project_member

ACTION = "hypothesis_registered"
DOMAIN = "quantgpt.hypothesis_registration/v1"


async def lock_research_registration(session: AsyncSession, actor_id: uuid.UUID,
                                     project_id: uuid.UUID) -> None:
    """Serialize hypothesis and evaluation registration on the same project.

    Evaluation registration must call this too, before inserting its trial row,
    so an uncommitted concurrent trial cannot race a hypothesis registration.
    Caller owns commit/rollback; a conflict must restart the whole transaction.
    """
    await require_project_member(session, actor_id, project_id, write=True)
    project = (await session.scalars(select(ResearchProject).where(ResearchProject.id == project_id)
                                    .execution_options(populate_existing=True))).one_or_none()
    if project is None:
        raise ProjectAccessError("Project not found or inaccessible")
    result = await session.execute(update(ResearchProject).where(ResearchProject.id == project.id,
                                                                 ResearchProject.revision == project.revision)
                                   .values(revision=project.revision + 1))
    if cast(CursorResult, result).rowcount != 1:
        raise ValueError("RESEARCH_REGISTRATION_CONFLICT: rollback and retry from fresh project state")


def _event_id(project_id: uuid.UUID, identity: str) -> uuid.UUID:
    if not re.fullmatch(r"[a-f0-9]{64}", identity):
        raise ValueError("Invalid definition hash")
    return uuid.uuid5(project_id, "hypothesis:" + identity)


def _read_event(event: ResearchAuditEvent, identity: str) -> dict[str, Any]:
    data = dict(event.payload)
    digest = data.pop("registration_hash", None)
    if event.action != ACTION or data.get("definition_hash") != identity or digest != content_hash(DOMAIN, data):
        raise ValueError("HYPOTHESIS_REGISTRATION_INTEGRITY_MISMATCH")
    registered_at = event.created_at
    if registered_at.tzinfo is None:  # SQLite stores the UTC value without its offset.
        registered_at = registered_at.replace(tzinfo=timezone.utc)
    return {**data, "registration_hash": digest, "audit_event_id": str(event.id),
            "project_id": str(event.project_id), "actor_id": str(event.actor_id),
            "registered_at": registered_at.isoformat()}


async def get_hypothesis(session: AsyncSession, actor_id: uuid.UUID, project_id: uuid.UUID,
                          identity: str) -> dict[str, Any] | None:
    await require_project_member(session, actor_id, project_id)
    event = await session.get(ResearchAuditEvent, _event_id(project_id, identity), populate_existing=True)
    return _read_event(event, identity) if event is not None else None


async def register_hypothesis(session: AsyncSession, actor_id: uuid.UUID, project_id: uuid.UUID,
                               definition: FactorDefinitionV1, *, economic_hypothesis: str,
                               frozen_baseline: EvaluationRef | None = None) -> dict[str, Any]:
    """Append once, before any trial for this definition is registered.

    Identical retries are allowed even after observation. Modifying the hypothesis
    or baseline never overwrites prior audit history or consumes a final window.
    """
    hypothesis = economic_hypothesis.strip()
    if not hypothesis or len(hypothesis) > 10000:
        raise ValueError("Economic hypothesis must contain 1 to 10000 characters")
    await lock_research_registration(session, actor_id, project_id)
    identity = definition_hash(definition)
    if frozen_baseline is not None:
        from quantgpt.research.evaluations import get_evaluation, verify_frozen_config

        if frozen_baseline.project_id != str(project_id):
            raise ProjectAccessError("Baseline not found or inaccessible")
        baseline = await get_evaluation(session, actor_id, project_id, frozen_baseline.evaluation_id)
        config = verify_frozen_config(baseline)
        if (frozen_baseline.evaluation_hash != baseline.evaluation_hash
                or frozen_baseline.definition_hash != baseline.definition_hash
                or frozen_baseline.backend != baseline.backend or frozen_baseline.scope != config.window.phase):
            raise ValueError("BASELINE_REFERENCE_INTEGRITY_MISMATCH")
        if not (baseline.result_summary or {}).get("performance_observed"):
            raise ValueError("BASELINE_EVALUATION_REQUIRED")
    payload = {"schema_version": "hypothesis_registration/v1", "definition_hash": identity,
               "economic_hypothesis": hypothesis,
               "frozen_baseline": frozen_baseline.model_dump(mode="json") if frozen_baseline else None}
    digest = content_hash(DOMAIN, payload)
    event_id = _event_id(project_id, identity)
    existing = await session.get(ResearchAuditEvent, event_id, populate_existing=True)
    if existing is not None:
        current = _read_event(existing, identity)
        if current["registration_hash"] != digest:
            raise ValueError("HYPOTHESIS_ALREADY_FROZEN: create a genuinely distinct preregistered definition")
        return current
    registered_trials = await session.scalar(select(func.count()).select_from(Experiment).where(
        Experiment.project_id == project_id, Experiment.definition_hash == identity))
    if registered_trials:
        raise ValueError("HYPOTHESIS_TOO_LATE: register before any evaluation of this definition")
    event = ResearchAuditEvent(id=event_id, project_id=project_id, actor_id=actor_id, action=ACTION,
                               payload={**payload, "registration_hash": digest})
    session.add(event)
    await session.flush()
    return _read_event(event, identity)
