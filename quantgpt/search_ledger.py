"""Search-attempt ledger helpers for factor iteration."""

from __future__ import annotations

import asyncio
import logging
import math
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, or_, select

from .expression_parser import normalize_expression
from .models import FactorSearchAttempt

logger = logging.getLogger(__name__)

SEARCH_PENALTY_LAMBDA = 2.0
_NUMBER_PATTERN = re.compile(r"(?<![a-z_])[-+]?\d+(?:\.\d+)?(?:e[-+]?\d+)?(?![a-z_])", re.IGNORECASE)


@dataclass(slots=True)
class AttemptCounts:
    expression_attempts: int = 0
    family_attempts: int = 0


def expression_key(expression: str) -> str:
    """Return canonical expression key for exact-repeat tracking."""
    return normalize_expression(expression or "")


def family_key(expression: str) -> str:
    """Return a coarse expression-family key that ignores standalone numeric parameters."""
    return _NUMBER_PATTERN.sub("<num>", expression_key(expression))


def build_scope_key(params: dict[str, Any] | None) -> str:
    params = params or {}
    parts = [
        str(params.get("universe", "")),
        str(params.get("start_date", "")),
        str(params.get("end_date", "")),
        str(params.get("holding_period", "")),
        str(params.get("n_groups", "")),
    ]
    return "|".join(parts)


def compute_search_penalty(
    prior_expression_attempts: int,
    prior_family_attempts: int,
    penalty_lambda: float = SEARCH_PENALTY_LAMBDA,
) -> float:
    """Compute a mild selection-stage penalty from prior exact/family attempts."""
    weighted = max(0.0, float(prior_expression_attempts) + 0.5 * float(prior_family_attempts))
    return round(max(0.0, penalty_lambda * math.log1p(weighted)), 4)


def apply_selection_penalty(raw_score: float | int | None, penalty: float | int | None) -> float:
    try:
        score = float(raw_score or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    try:
        penalty_value = float(penalty or 0.0)
    except (TypeError, ValueError):
        penalty_value = 0.0
    return round(max(0.0, score - penalty_value), 4)


def summarize_attempts(attempts: list[dict[str, Any]]) -> dict[str, int]:
    successful = [a for a in attempts if not a.get("failed")]
    return {
        "total_attempts": len(attempts),
        "failed_attempts": sum(1 for a in attempts if a.get("failed")),
        "mutation_attempts": sum(1 for a in attempts if a.get("from_mutation")),
        "crossover_attempts": sum(1 for a in attempts if a.get("from_crossover")),
        "advanced_attempts": sum(1 for a in successful if a.get("entered_next_round")),
    }


def attempt_payload(
    *,
    user_id: str,
    task_id: str | None,
    parent_task_id: str | None,
    generation_index: int,
    expression: str,
    params: dict[str, Any] | None,
    source_strategy: str | None,
    from_mutation: bool,
    from_crossover: bool,
    prior_expression_attempts: int = 0,
    prior_family_attempts: int = 0,
    status: str = "generated",
    failed: bool = False,
    failure_stage: str | None = None,
    failure_reason: str | None = None,
    entered_next_round: bool = False,
    raw_score: float | None = None,
    selection_score: float | None = None,
    search_penalty: float | None = None,
) -> dict[str, Any]:
    params = dict(params or {})
    penalty = compute_search_penalty(
        prior_expression_attempts,
        prior_family_attempts,
    ) if search_penalty is None else float(search_penalty)
    resolved_selection = apply_selection_penalty(raw_score, penalty) if selection_score is None else float(selection_score)
    expr_key = expression_key(expression)
    fam_key = family_key(expression)
    return {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "task_id": task_id,
        "parent_task_id": parent_task_id,
        "generation_index": generation_index,
        "expression": expression,
        "expression_key": expr_key,
        "family_key": fam_key,
        "scope_key": build_scope_key(params),
        "params": params,
        "start_date": params.get("start_date"),
        "end_date": params.get("end_date"),
        "universe": params.get("universe"),
        "source_strategy": source_strategy,
        "from_mutation": from_mutation,
        "from_crossover": from_crossover,
        "status": status,
        "failed": failed,
        "failure_stage": failure_stage,
        "failure_reason": failure_reason,
        "entered_next_round": entered_next_round,
        "raw_score": raw_score,
        "selection_score": resolved_selection,
        "search_penalty": penalty,
        "prior_expression_attempts": prior_expression_attempts,
        "prior_family_attempts": prior_family_attempts,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def count_local_attempts(attempts: list[dict[str, Any]], expression: str, params: dict[str, Any] | None) -> AttemptCounts:
    expr_key = expression_key(expression)
    fam_key = family_key(expression)
    scope_key = build_scope_key(params)
    expression_count = 0
    family_count = 0
    for attempt in attempts:
        if attempt.get("scope_key") != scope_key:
            continue
        if attempt.get("expression_key") == expr_key:
            expression_count += 1
        if attempt.get("family_key") == fam_key:
            family_count += 1
    return AttemptCounts(expression_attempts=expression_count, family_attempts=family_count)


async def count_db_attempts(
    session,
    *,
    user_id: str,
    expression: str,
    params: dict[str, Any] | None,
    exclude_task_id: str | None = None,
) -> AttemptCounts:
    user_uuid = uuid.UUID(user_id) if isinstance(user_id, str) else user_id
    expr_key = expression_key(expression)
    fam_key = family_key(expression)
    scope_key = build_scope_key(params)
    base_conditions = [
        FactorSearchAttempt.user_id == user_uuid,
        FactorSearchAttempt.scope_key == scope_key,
    ]
    if exclude_task_id:
        base_conditions.append(
            or_(FactorSearchAttempt.task_id.is_(None), FactorSearchAttempt.task_id != exclude_task_id)
        )
    expression_count = await session.scalar(
        select(func.count()).select_from(FactorSearchAttempt).where(
            *base_conditions,
            FactorSearchAttempt.expression_key == expr_key,
        )
    )
    family_count = await session.scalar(
        select(func.count()).select_from(FactorSearchAttempt).where(
            *base_conditions,
            FactorSearchAttempt.family_key == fam_key,
        )
    )
    return AttemptCounts(
        expression_attempts=int(expression_count or 0),
        family_attempts=int(family_count or 0),
    )


def count_persisted_attempts(
    *,
    user_id: str,
    expression: str,
    params: dict[str, Any] | None,
    exclude_task_id: str | None = None,
) -> AttemptCounts:
    from . import task_store
    from .db import _get_session_factory

    async def _count() -> AttemptCounts:
        factory = _get_session_factory()
        async with factory() as session:
            return await count_db_attempts(
                session,
                user_id=user_id,
                expression=expression,
                params=params,
                exclude_task_id=exclude_task_id,
            )

    loop = task_store.main_loop
    if loop and loop.is_running():
        future = asyncio.run_coroutine_threadsafe(_count(), loop)
        try:
            return future.result(timeout=5)
        except Exception as exc:
            logger.debug("factor search attempt count unavailable: %s", exc)
            return AttemptCounts()

    try:
        return asyncio.run(_count())
    except Exception as exc:
        logger.debug("factor search attempt count unavailable: %s", exc)
        return AttemptCounts()


async def persist_attempt_async(attempt: dict[str, Any]) -> None:
    from .db import _get_session_factory

    factory = _get_session_factory()
    async with factory() as session:
        try:
            created_at = attempt.get("created_at")
            if isinstance(created_at, str):
                created_at = datetime.fromisoformat(created_at)
            if not isinstance(created_at, datetime):
                created_at = datetime.now(timezone.utc)
            row = FactorSearchAttempt(
                id=uuid.UUID(attempt["id"]),
                user_id=uuid.UUID(attempt["user_id"]) if isinstance(attempt.get("user_id"), str) else attempt["user_id"],
                task_id=attempt.get("task_id"),
                parent_task_id=attempt.get("parent_task_id"),
                generation_index=attempt.get("generation_index", 0),
                expression=attempt.get("expression", ""),
                expression_key=attempt.get("expression_key", ""),
                family_key=attempt.get("family_key", ""),
                scope_key=attempt.get("scope_key", ""),
                params=attempt.get("params"),
                start_date=attempt.get("start_date"),
                end_date=attempt.get("end_date"),
                universe=attempt.get("universe"),
                source_strategy=attempt.get("source_strategy"),
                from_mutation=bool(attempt.get("from_mutation")),
                from_crossover=bool(attempt.get("from_crossover")),
                status=attempt.get("status", "generated"),
                failed=bool(attempt.get("failed")),
                failure_stage=attempt.get("failure_stage"),
                failure_reason=attempt.get("failure_reason"),
                entered_next_round=bool(attempt.get("entered_next_round")),
                raw_score=attempt.get("raw_score"),
                selection_score=attempt.get("selection_score"),
                search_penalty=attempt.get("search_penalty", 0.0),
                prior_expression_attempts=attempt.get("prior_expression_attempts", 0),
                prior_family_attempts=attempt.get("prior_family_attempts", 0),
                created_at=created_at,
            )
            await session.merge(row)
            await session.commit()
        except Exception as exc:
            await session.rollback()
            logger.warning("factor search attempt persist failed: %s", exc)


def persist_attempt(attempt: dict[str, Any]) -> None:
    """Best-effort persistence for sync iteration worker threads."""
    from . import task_store

    loop = task_store.main_loop
    if loop and loop.is_running():
        future = asyncio.run_coroutine_threadsafe(persist_attempt_async(attempt), loop)
        try:
            future.result(timeout=10)
        except Exception as exc:
            logger.warning("factor search attempt persist error: %s", exc)
        return

    try:
        asyncio.run(persist_attempt_async(attempt))
    except Exception as exc:
        logger.warning("factor search attempt persist unavailable: %s", exc)
