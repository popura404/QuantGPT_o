"""Read-only factor discovery against authorized evidence and pending proposals."""

from __future__ import annotations

import ast
import re
import uuid
from collections import defaultdict
from functools import lru_cache

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from quantgpt.expression_parser import ExpressionParser
from quantgpt.models import (
    Experiment,
    FactorPoolEntry,
    FactorSearchAttempt,
    ProjectMember,
    ResearchDirection,
    ResearchRound,
    SavedFactor,
    SubmittedAlpha,
    Task,
)
from quantgpt.research.access_ledger import experiment_scope
from quantgpt.research.projects import require_project_member
from quantgpt.search_ledger import expression_key, family_key


@lru_cache(maxsize=8192)
def _formula_keys(expression: str) -> tuple[str, str]:
    # Recompute identity from the original expression, including aliases added
    # to the evaluator since an older ledger entry was created.
    key = expression_key(expression)
    key = re.sub(r"\b([a-z_]\w*)(?=\()", lambda m: ExpressionParser._OPERATOR_ALIASES.get(m[1], m[1]), key)
    try:
        # Drop redundant parentheses without commuting arguments or changing signs.
        key = expression_key(ast.unparse(ast.parse(key, mode="eval").body))
    except (ValueError, SyntaxError, RecursionError):
        pass  # DSL-only syntax still has the ledger's lexical identity.
    return key, family_key(key)


async def check_factor_novelty(session: AsyncSession, actor_id: uuid.UUID, project_id: uuid.UUID, *,
                               expressions: list[str]) -> dict:
    """Compare formula identities; an expression match alone does not authorize reusing its performance."""
    await require_project_member(session, actor_id, project_id)
    if not 1 <= len(expressions) <= 100 or any(not expr.strip() for expr in expressions):
        raise ValueError("Supply between 1 and 100 nonempty factor expressions")
    candidates = []
    families: dict[str, list[int]] = defaultdict(list)
    keys = [_formula_keys(expr) for expr in expressions]
    for index, expr in enumerate(expressions):
        families[keys[index][1]].append(index)
        candidates.append({"index": index, "expression": expr, "known": False, "exact_count": 0,
                           "family_count": 0, "exact_matches": [], "family_matches": []})

    def record(expr: str, source: str, identifier: object, *, scope: object = None, **detail) -> None:
        key, family = _formula_keys(expr)
        for index in families.get(family, ()):
            exact = keys[index][0] == key
            candidate = candidates[index]
            kind = "exact" if exact else "family"
            candidate["known"] |= exact
            candidate[f"{kind}_count"] += 1
            matches = candidate[f"{kind}_matches"]
            if len(matches) < 5:
                matches.append({"source": source, "id": str(identifier), "expression": expr,
                                "project_id": str(scope) if scope else None, **detail})

    visible = select(ProjectMember.project_id).where(ProjectMember.user_id == actor_id)
    # Load small projections, never task results, market data, returns or private notes.
    pending = select(Task.id, Task.status, Task.params["direction_id"].as_string(), Task.params["definitions"]).where(
        Task.project_id == project_id, Task.task_type == "research_evaluation",
        Task.params["kind"].as_string() == "factor", Task.params["config"]["backend"].as_string() == "local",
        Task.params["config"]["window"]["phase"].as_string() == "selection",
        or_(Task.status.in_(("queued", "pending", "running")), and_(Task.status == "interrupted", Task.retryable.is_(True))),
    ).order_by(Task.id)
    for task_id, state, direction_id, definitions in await session.execute(pending):
        for definition in definitions or []:
            record(definition["expression"], "pending_task", task_id, scope=project_id, direction_id=direction_id, status=state)

    observed = select(Experiment.expression, Experiment.experiment_id, Experiment.project_id, Experiment.backend).where(
        experiment_scope(actor_id),
        or_(and_(Experiment.definition_hash.is_not(None),
                 Experiment.evaluation_config["config"]["window"]["phase"].as_string().in_(("selection", "platform")),
                 Experiment.result_summary["performance_observed"].as_boolean().is_(True)),
            and_(Experiment.definition_hash.is_(None), Experiment.strategy_id.is_(None), Experiment.strategy_run_id.is_(None),
                 Experiment.status.in_(("backtested_train", "validated_oos", "rolling_checked", "anti_overfit_checked",
                                        "multiple_testing_checked", "candidate", "exported")))),
    ).order_by(Experiment.experiment_id)
    for expr, eid, scope, backend in await session.execute(observed):
        record(expr, "evaluation", eid, scope=scope, backend=backend)

    pool = select(FactorPoolEntry.expression, FactorPoolEntry.id, FactorPoolEntry.project_id).where(
        or_(FactorPoolEntry.project_id.in_(visible),
            and_(FactorPoolEntry.project_id.is_(None), FactorPoolEntry.owner_user_id == actor_id))).order_by(FactorPoolEntry.id)
    for expr, entry_id, scope in await session.execute(pool):
        record(expr, "factor_pool", entry_id, scope=scope)
    favorites = select(SavedFactor.expression, SavedFactor.id).where(SavedFactor.user_id == actor_id).order_by(SavedFactor.id)
    for expr, favorite_id in await session.execute(favorites):
        record(expr, "personal_favorite", favorite_id)
    submitted = select(SubmittedAlpha.expression, SubmittedAlpha.id).where(
        SubmittedAlpha.user_id == actor_id).order_by(SubmittedAlpha.id)
    for expr, alpha_id in await session.execute(submitted):
        record(expr, "submitted_alpha", alpha_id)

    attempts = select(FactorSearchAttempt.expression, FactorSearchAttempt.id, Task.project_id).outerjoin(
        Task, Task.id == FactorSearchAttempt.task_id).where(
        FactorSearchAttempt.status == "success",
        FactorSearchAttempt.failed.is_(False), FactorSearchAttempt.raw_score.is_not(None),
        or_(Task.project_id.in_(visible), and_(Task.project_id.is_(None), FactorSearchAttempt.user_id == actor_id)),
    ).order_by(FactorSearchAttempt.id)
    for expr, attempt_id, scope in await session.execute(attempts):
        record(expr, "search_attempt", attempt_id, scope=scope)

    # Some historical workers saved only the result JSON. Imported observations
    # are project evidence even when the original personal task belongs to another member.
    sources = await session.scalars(select(ResearchDirection.legacy_sources).join(ResearchRound).where(
        ResearchRound.project_id == project_id).order_by(ResearchDirection.id))
    imported = {}
    for direction_sources in sources:
        for source in direction_sources:
            for trial in source["trials"]:
                if trial.get("expression_key"):
                    imported[trial["trial_key"]] = trial["expression_key"]
    for trial_id, expr in sorted(imported.items()):
        record(expr, "imported_trial", trial_id, scope=project_id)

    prior: dict[str, int] = {}
    for candidate, (key, _) in zip(candidates, keys, strict=True):
        if key in prior:
            candidate["known"] = True
            candidate["duplicate_of_index"] = prior[key]
        else:
            prior[key] = candidate["index"]
    return {"project_id": str(project_id), "candidates": candidates,
            "new_indices": [item["index"] for item in candidates if not item["known"]]}
