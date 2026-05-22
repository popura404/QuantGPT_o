"""Experiment ledger foundation for Agent-driven factor research."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .expression_parser import normalize_expression
from .models import (
    Experiment,
    ExperimentArtifact,
    ExperimentResult,
    ExportEvent,
    FactorRegistry,
    PromotionEvent,
)
from .search_ledger import family_key

EXPERIMENT_ID_PREFIX = "exp"
FACTOR_HASH_PREFIX = "fh"

EXPERIMENT_STATUSES = (
    "draft",
    "parsed",
    "parse_failed",
    "data_quality_failed",
    "backtested_train",
    "validated_oos",
    "rolling_checked",
    "anti_overfit_checked",
    "multiple_testing_checked",
    "candidate",
    "rejected",
    "exported",
    "archived",
)

TERMINAL_STATUSES = {"parse_failed", "data_quality_failed", "rejected", "exported", "archived"}

ALLOWED_STATUS_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"parsed", "parse_failed", "data_quality_failed", "rejected", "archived"},
    "parsed": {"data_quality_failed", "backtested_train", "validated_oos", "rejected", "archived"},
    "backtested_train": {"validated_oos", "rolling_checked", "anti_overfit_checked", "rejected", "archived"},
    "validated_oos": {"rolling_checked", "anti_overfit_checked", "multiple_testing_checked", "candidate", "rejected", "archived"},
    "rolling_checked": {"anti_overfit_checked", "multiple_testing_checked", "candidate", "rejected", "archived"},
    "anti_overfit_checked": {"multiple_testing_checked", "candidate", "rejected", "archived"},
    "multiple_testing_checked": {"candidate", "rejected", "archived"},
    "candidate": {"exported", "rejected", "archived"},
}


class ExperimentLedgerError(ValueError):
    """Raised for invalid ledger input or lifecycle transitions."""


def compute_factor_hash(expression: str, config: dict[str, Any] | None = None, **kwargs: Any) -> str:
    """Compute a deterministic factor hash from expression plus research configuration."""
    merged = dict(config or {})
    merged.update(kwargs)
    normalized = normalize_expression(expression or "")
    payload = {
        "expression_normalized": normalized,
        "config": _canonical_json_value(merged),
    }
    return _prefixed_hash(FACTOR_HASH_PREFIX, payload)


def compute_config_hash(config: dict[str, Any] | None = None, **kwargs: Any) -> str:
    """Compute a deterministic hash for non-expression experiment configuration."""
    merged = dict(config or {})
    merged.update(kwargs)
    return _prefixed_hash("cfg", _canonical_json_value(merged))


def new_experiment_id() -> str:
    return f"{EXPERIMENT_ID_PREFIX}_{uuid.uuid4().hex}"


async def record_experiment(
    session: AsyncSession,
    *,
    expression: str,
    params: dict[str, Any] | None = None,
    status: str = "draft",
    experiment_id: str | None = None,
    run_id: str | None = None,
    parent_run_id: str | None = None,
    user_id: str | uuid.UUID | None = None,
    task_id: str | None = None,
    parent_experiment_id: str | None = None,
    factor_id: str | None = None,
    factor_hash: str | None = None,
    strategy_spec_version: str | None = None,
    strategy_id: str | uuid.UUID | None = None,
    strategy_run_id: str | uuid.UUID | None = None,
    created_by: str | None = None,
    git_commit: str | None = None,
    result_summary: dict[str, Any] | None = None,
    failure_reason: str | None = None,
    notes: str | None = None,
) -> Experiment:
    """Insert an experiment row and upsert its factor registry entry."""
    if status not in EXPERIMENT_STATUSES:
        raise ExperimentLedgerError(f"Unknown experiment status: {status}")
    params = dict(params or {})
    normalized = normalize_expression(expression or "")
    resolved_factor_hash = factor_hash or compute_factor_hash(expression, _hash_config_from_params(params))
    row = Experiment(
        experiment_id=experiment_id or new_experiment_id(),
        run_id=run_id,
        parent_run_id=parent_run_id,
        user_id=_coerce_uuid(user_id),
        task_id=task_id,
        parent_experiment_id=parent_experiment_id,
        factor_id=factor_id,
        factor_hash=resolved_factor_hash,
        expression=expression,
        expression_normalized=normalized,
        strategy_spec_version=strategy_spec_version,
        strategy_id=_coerce_uuid(strategy_id),
        strategy_run_id=_coerce_uuid(strategy_run_id),
        universe=params.get("universe"),
        market=params.get("market") or params.get("region") or "a_share",
        asset_class=params.get("asset_class") or "equity",
        data_source=params.get("data_source"),
        data_version=params.get("data_version"),
        data_snapshot_id=params.get("data_snapshot_id"),
        adjustment_type=params.get("adjustment_type") or params.get("adjustment"),
        industry_neutralization=params.get("industry_neutralization", params.get("neutralize_industry")),
        size_neutralization=params.get("size_neutralization", params.get("neutralize_cap")),
        cost_model=params.get("cost_model"),
        rebalance_frequency=params.get("rebalance_frequency"),
        holding_period=params.get("holding_period"),
        train_period=params.get("train_period"),
        validation_period=params.get("validation_period"),
        test_period=params.get("test_period"),
        direction_mode=params.get("direction_mode"),
        direction_policy=params.get("direction_policy"),
        research_mode=params.get("research_mode"),
        random_seed=params.get("random_seed"),
        status=status,
        promotion_stage=params.get("promotion_stage"),
        created_by=created_by,
        git_commit=git_commit,
        config_hash=compute_config_hash(_hash_config_from_params(params)),
        result_summary=result_summary,
        failure_reason=failure_reason,
        notes=notes,
    )
    session.add(row)
    await _upsert_factor_registry(session, row)
    await session.flush()
    return row


async def get_experiment(session: AsyncSession, experiment_id: str) -> Experiment | None:
    result = await session.execute(select(Experiment).where(Experiment.experiment_id == experiment_id))
    return result.scalar_one_or_none()


async def list_experiments(
    session: AsyncSession,
    *,
    status: str | None = None,
    universe: str | None = None,
    factor_hash: str | None = None,
    limit: int = 50,
) -> list[Experiment]:
    stmt = select(Experiment).order_by(Experiment.created_at.desc()).limit(limit)
    if status is not None:
        stmt = stmt.where(Experiment.status == status)
    if universe is not None:
        stmt = stmt.where(Experiment.universe == universe)
    if factor_hash is not None:
        stmt = stmt.where(Experiment.factor_hash == factor_hash)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def transition_status(
    session: AsyncSession,
    experiment_id: str,
    new_status: str,
    *,
    failure_reason: str | None = None,
    promotion_stage: str | None = None,
) -> Experiment:
    experiment = await get_experiment(session, experiment_id)
    if experiment is None:
        raise ExperimentLedgerError(f"Experiment not found: {experiment_id}")
    assert_valid_transition(experiment.status, new_status)
    experiment.status = new_status
    experiment.updated_at = datetime.now(timezone.utc)
    if failure_reason is not None:
        experiment.failure_reason = failure_reason
    if promotion_stage is not None:
        experiment.promotion_stage = promotion_stage
    await session.flush()
    return experiment


def assert_valid_transition(current_status: str, new_status: str) -> None:
    if new_status not in EXPERIMENT_STATUSES:
        raise ExperimentLedgerError(f"Unknown experiment status: {new_status}")
    if current_status == new_status:
        return
    if current_status in TERMINAL_STATUSES:
        raise ExperimentLedgerError(f"Cannot transition terminal experiment from {current_status} to {new_status}")
    allowed = ALLOWED_STATUS_TRANSITIONS.get(current_status, set())
    if new_status not in allowed:
        raise ExperimentLedgerError(f"Illegal experiment transition: {current_status} -> {new_status}")


async def record_experiment_result(
    session: AsyncSession,
    *,
    experiment_id: str,
    stage: str,
    validation_stage: str | None = None,
    train_period: Any = None,
    validation_period: Any = None,
    test_period: Any = None,
    direction_policy: str | None = None,
    metrics: dict[str, Any] | None = None,
    oos_score: dict[str, Any] | None = None,
    data_quality: dict[str, Any] | None = None,
    failure_reason: str | None = None,
) -> ExperimentResult:
    row = ExperimentResult(
        experiment_id=experiment_id,
        stage=stage,
        validation_stage=validation_stage,
        train_period=train_period,
        validation_period=validation_period,
        test_period=test_period,
        direction_policy=direction_policy,
        metrics=metrics,
        oos_score=oos_score,
        data_quality=data_quality,
        failure_reason=failure_reason,
    )
    session.add(row)
    await session.flush()
    return row


async def record_experiment_artifact(
    session: AsyncSession,
    *,
    experiment_id: str,
    artifact_type: str,
    uri: str,
    content_hash: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> ExperimentArtifact:
    row = ExperimentArtifact(
        experiment_id=experiment_id,
        artifact_type=artifact_type,
        uri=uri,
        content_hash=content_hash,
        artifact_metadata=metadata,
    )
    session.add(row)
    await session.flush()
    return row


async def record_promotion_event(
    session: AsyncSession,
    *,
    experiment_id: str,
    boundary: str,
    decision: str,
    blockers: list[str] | None = None,
    provenance: dict[str, Any] | None = None,
) -> PromotionEvent:
    row = PromotionEvent(
        experiment_id=experiment_id,
        boundary=boundary,
        decision=decision,
        blockers=blockers,
        provenance=provenance,
    )
    session.add(row)
    await session.flush()
    return row


async def record_export_event(
    session: AsyncSession,
    *,
    experiment_id: str,
    schema_version: str,
    export_path: str | None = None,
    payload: dict[str, Any] | None = None,
    payload_hash: str | None = None,
) -> ExportEvent:
    row = ExportEvent(
        experiment_id=experiment_id,
        schema_version=schema_version,
        export_path=export_path,
        payload_hash=payload_hash or (_prefixed_hash("payload", payload) if payload is not None else None),
    )
    session.add(row)
    await session.flush()
    return row


async def summarize_trial_counts(
    session: AsyncSession,
    *,
    user_id: str | uuid.UUID | None = None,
    universe: str | None = None,
    factor_hash: str | None = None,
    operator_family: str | None = None,
) -> dict[str, int]:
    base = select(func.count()).select_from(Experiment)
    project_conditions = []
    resolved_user_id = _coerce_uuid(user_id)
    if resolved_user_id is not None:
        project_conditions.append(Experiment.user_id == resolved_user_id)

    total = await session.scalar(base.where(*project_conditions))
    same_factor = (
        await session.scalar(base.where(*project_conditions, Experiment.factor_hash == factor_hash))
        if factor_hash
        else 0
    )
    same_universe = (
        await session.scalar(base.where(*project_conditions, Experiment.universe == universe))
        if universe
        else 0
    )

    same_family = 0
    if operator_family is not None:
        registry_stmt = (
            select(func.count())
            .select_from(FactorRegistry)
            .where(FactorRegistry.operator_family == operator_family)
        )
        same_family = await session.scalar(registry_stmt)

    return {
        "total_trials_in_project": int(total or 0),
        "trials_in_same_universe": int(same_universe or 0),
        "trials_in_same_factor_family": int(same_family or 0),
        "trials_by_factor_hash": int(same_factor or 0),
    }


async def _upsert_factor_registry(session: AsyncSession, experiment: Experiment) -> None:
    result = await session.execute(
        select(FactorRegistry).where(FactorRegistry.factor_hash == experiment.factor_hash)
    )
    registry = result.scalar_one_or_none()
    if registry is None:
        session.add(FactorRegistry(
            factor_hash=experiment.factor_hash,
            expression_normalized=experiment.expression_normalized,
            family_key=family_key(experiment.expression),
            operator_family=_operator_family(experiment.expression_normalized),
            first_experiment_id=experiment.experiment_id,
            latest_experiment_id=experiment.experiment_id,
        ))
        return
    registry.latest_experiment_id = experiment.experiment_id
    registry.updated_at = datetime.now(timezone.utc)


def _hash_config_from_params(params: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "universe",
        "market",
        "asset_class",
        "data_source",
        "data_version",
        "data_snapshot_id",
        "adjustment_type",
        "adjustment",
        "industry_neutralization",
        "neutralize_industry",
        "size_neutralization",
        "neutralize_cap",
        "cost_model",
        "rebalance_frequency",
        "holding_period",
        "n_groups",
        "train_period",
        "validation_period",
        "test_period",
        "direction_mode",
        "direction_policy",
        "validation_stage",
        "oos",
    )
    return {key: params.get(key) for key in keys if key in params and params.get(key) is not None}


def _prefixed_hash(prefix: str, payload: Any) -> str:
    raw = json.dumps(_canonical_json_value(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    return f"{prefix}_{digest}"


def _canonical_json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _canonical_json_value(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple)):
        return [_canonical_json_value(v) for v in value]
    if isinstance(value, set):
        return [_canonical_json_value(v) for v in sorted(value, key=str)]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def _coerce_uuid(value: str | uuid.UUID | None) -> uuid.UUID | None:
    if value is None or isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def _operator_family(expression_normalized: str) -> str:
    operators = []
    token = []
    for char in expression_normalized:
        if char.isalpha() or char == "_":
            token.append(char)
            continue
        if char == "(" and token:
            operators.append("".join(token))
        token = []
    return ",".join(dict.fromkeys(operators)) if operators else "raw_field"
