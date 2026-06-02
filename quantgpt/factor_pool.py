"""Shared factor-pool persistence helpers for REST and MCP."""

from __future__ import annotations

import re
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .experiment_ledger import compute_factor_hash
from .expression_parser import normalize_expression
from .models import FactorPoolEntry, User

MCP_SYSTEM_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")
MCP_SYSTEM_USER_EMAIL = "mcp@system.internal"

DEFAULT_CATEGORY_TAG = "category:uncategorized"
POOL_STATUSES = (
    "accepted",
    "watchlist",
    "rejected",
    "insufficient_data",
    "runtime_failed",
)


class FactorPoolError(ValueError):
    """Base error for factor-pool service operations."""


class FactorPoolValidationError(FactorPoolError):
    """Raised when a factor-pool payload is invalid."""


class FactorPoolNotFoundError(FactorPoolError):
    """Raised when a factor-pool entry does not exist for the owner."""


def normalize_tag(tag: str | None) -> str | None:
    value = re.sub(r"\s+", " ", str(tag or "").strip().lower())
    return value or None


def normalize_category_tag(category: str | None) -> str | None:
    value = normalize_tag(category)
    if not value:
        return None
    if value.startswith("category:"):
        suffix = normalize_tag(value.split(":", 1)[1])
        return f"category:{suffix}" if suffix else None
    return f"category:{value}"


def normalize_tags(tags: list[str] | None, category: str | None = None) -> tuple[list[str], str]:
    raw_tags = [tag for tag in (normalize_tag(item) for item in (tags or [])) if tag]
    category_tag = normalize_category_tag(category)
    if category_tag is None:
        category_tag = next((tag for tag in raw_tags if tag.startswith("category:")), None) or DEFAULT_CATEGORY_TAG

    normalized = [category_tag]
    normalized.extend(tag for tag in raw_tags if not tag.startswith("category:"))
    return list(dict.fromkeys(normalized)), category_tag


def validate_pool_status(pool_status: str | None) -> str:
    value = normalize_tag(pool_status) or "watchlist"
    if value not in POOL_STATUSES:
        allowed = ", ".join(POOL_STATUSES)
        raise FactorPoolValidationError(f"Unknown factor pool status: {pool_status}. Allowed: {allowed}")
    return value


async def ensure_mcp_system_user(session: AsyncSession) -> User:
    result = await session.execute(select(User).where(User.id == MCP_SYSTEM_USER_ID))
    user = result.scalar_one_or_none()
    if user is not None:
        return user
    user = User(
        id=MCP_SYSTEM_USER_ID,
        email=MCP_SYSTEM_USER_EMAIL,
        nickname="MCP System",
        is_active=True,
        subscribe_weekly=False,
    )
    session.add(user)
    await session.flush()
    return user


async def save_factor_pool_entry(
    session: AsyncSession,
    *,
    owner_user_id: str | uuid.UUID,
    data: dict[str, Any],
    entry_id: str | uuid.UUID | None = None,
) -> tuple[FactorPoolEntry, bool]:
    """Create or update a pool entry using factor_hash/expression identity."""
    owner_id = _coerce_uuid(owner_user_id, "owner_user_id")
    payload = dict(data)
    if entry_id is not None:
        row = await get_factor_pool_entry(session, owner_user_id=owner_id, entry_id=entry_id)
        _apply_entry_fields(row, payload, creating=False)
        await session.flush()
        return row, False

    expression = _required_expression(payload.get("expression"))
    expression_normalized = normalize_expression(expression)
    factor_hash = _resolve_factor_hash(payload, expression)

    row = await _find_upsert_target(
        session,
        owner_user_id=owner_id,
        factor_hash=factor_hash,
        expression_normalized=expression_normalized,
        universe=payload.get("universe"),
        holding_period=payload.get("holding_period"),
    )
    created = row is None
    if row is None:
        row = FactorPoolEntry(id=uuid.uuid4(), owner_user_id=owner_id)
        session.add(row)
    _apply_entry_fields(row, payload, creating=created)
    await session.flush()
    return row, created


async def get_factor_pool_entry(
    session: AsyncSession,
    *,
    owner_user_id: str | uuid.UUID,
    entry_id: str | uuid.UUID,
) -> FactorPoolEntry:
    owner_id = _coerce_uuid(owner_user_id, "owner_user_id")
    row_id = _coerce_uuid(entry_id, "entry_id")
    result = await session.execute(
        select(FactorPoolEntry).where(
            FactorPoolEntry.id == row_id,
            FactorPoolEntry.owner_user_id == owner_id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise FactorPoolNotFoundError(f"Factor pool entry not found: {entry_id}")
    return row


async def update_factor_pool_entry(
    session: AsyncSession,
    *,
    owner_user_id: str | uuid.UUID,
    entry_id: str | uuid.UUID,
    data: dict[str, Any],
) -> FactorPoolEntry:
    row = await get_factor_pool_entry(session, owner_user_id=owner_user_id, entry_id=entry_id)
    _apply_entry_fields(row, dict(data), creating=False)
    await session.flush()
    return row


async def delete_factor_pool_entry(
    session: AsyncSession,
    *,
    owner_user_id: str | uuid.UUID,
    entry_id: str | uuid.UUID,
) -> None:
    row = await get_factor_pool_entry(session, owner_user_id=owner_user_id, entry_id=entry_id)
    await session.delete(row)
    await session.flush()


async def list_factor_pool_entries(
    session: AsyncSession,
    *,
    owner_user_id: str | uuid.UUID,
    pool_status: str | None = None,
    category: str | None = None,
    tag: str | None = None,
    tags: list[str] | None = None,
    universe: str | None = None,
    market: str | None = None,
    factor_hash: str | None = None,
    experiment_id: str | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[FactorPoolEntry], int]:
    owner_id = _coerce_uuid(owner_user_id, "owner_user_id")
    stmt = select(FactorPoolEntry).where(FactorPoolEntry.owner_user_id == owner_id)

    if pool_status:
        stmt = stmt.where(FactorPoolEntry.pool_status == validate_pool_status(pool_status))
    if category:
        stmt = stmt.where(FactorPoolEntry.category_tag == (normalize_category_tag(category) or DEFAULT_CATEGORY_TAG))
    if universe:
        stmt = stmt.where(FactorPoolEntry.universe == universe)
    if market:
        stmt = stmt.where(FactorPoolEntry.market == market)
    if factor_hash:
        stmt = stmt.where(FactorPoolEntry.factor_hash == factor_hash)
    if experiment_id:
        stmt = stmt.where(FactorPoolEntry.experiment_id == experiment_id)
    if q and q.strip():
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                FactorPoolEntry.expression.ilike(pattern),
                FactorPoolEntry.name.ilike(pattern),
                FactorPoolEntry.note.ilike(pattern),
                FactorPoolEntry.main_reason.ilike(pattern),
            )
        )

    stmt = stmt.order_by(desc(FactorPoolEntry.updated_at), desc(FactorPoolEntry.created_at))
    result = await session.execute(stmt)
    rows = list(result.scalars().all())

    tag_filters = _normalize_filter_tags(tag=tag, tags=tags)
    if tag_filters:
        rows = [row for row in rows if _row_has_all_tags(row, tag_filters)]

    bounded_limit = _coerce_int(limit, default=50, minimum=1, maximum=200)
    bounded_offset = _coerce_int(offset, default=0, minimum=0)
    total = len(rows)
    return rows[bounded_offset:bounded_offset + bounded_limit], total


async def list_factor_pool_tags(
    session: AsyncSession,
    *,
    owner_user_id: str | uuid.UUID,
    pool_status: str | None = None,
    universe: str | None = None,
    market: str | None = None,
) -> dict[str, Any]:
    owner_id = _coerce_uuid(owner_user_id, "owner_user_id")
    stmt = select(FactorPoolEntry).where(FactorPoolEntry.owner_user_id == owner_id)
    if pool_status:
        stmt = stmt.where(FactorPoolEntry.pool_status == validate_pool_status(pool_status))
    if universe:
        stmt = stmt.where(FactorPoolEntry.universe == universe)
    if market:
        stmt = stmt.where(FactorPoolEntry.market == market)
    result = await session.execute(stmt)
    rows = list(result.scalars().all())
    total = len(rows)
    tag_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    for row in rows:
        for tag in row.tags or []:
            normalized = normalize_tag(tag)
            if normalized:
                tag_counts[normalized] += 1
        if row.category_tag:
            category_counts[row.category_tag] += 1
        if row.pool_status:
            status_counts[row.pool_status] += 1
    return {
        "total": total,
        "tags": [{"tag": tag, "count": count} for tag, count in tag_counts.most_common()],
        "categories": [
            {"category_tag": tag, "category": tag.split(":", 1)[1], "count": count}
            for tag, count in category_counts.most_common()
        ],
        "statuses": [{"status": status, "count": count} for status, count in status_counts.most_common()],
    }


def factor_pool_entry_to_dict(row: FactorPoolEntry) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "owner_user_id": str(row.owner_user_id),
        "expression": row.expression,
        "expression_normalized": row.expression_normalized,
        "name": row.name,
        "note": row.note,
        "main_reason": row.main_reason,
        "tags": row.tags or [],
        "category_tag": row.category_tag,
        "category": row.category_tag.split(":", 1)[1] if row.category_tag and ":" in row.category_tag else None,
        "pool_status": row.pool_status,
        "factor_hash": row.factor_hash,
        "experiment_id": row.experiment_id,
        "task_id": row.task_id,
        "market": row.market,
        "universe": row.universe,
        "holding_period": row.holding_period,
        "validation_stage": row.validation_stage,
        "metrics": row.metrics,
        "backtest_summary": row.backtest_summary,
        "params": row.params,
        "validation_provenance": row.validation_provenance,
        "report_url": row.report_url,
        "factor_card_path": row.factor_card_path,
        "source": row.source,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


async def _find_upsert_target(
    session: AsyncSession,
    *,
    owner_user_id: uuid.UUID,
    factor_hash: str | None,
    expression_normalized: str,
    universe: str | None,
    holding_period: int | None,
) -> FactorPoolEntry | None:
    if factor_hash:
        result = await session.execute(
            select(FactorPoolEntry).where(
                FactorPoolEntry.owner_user_id == owner_user_id,
                FactorPoolEntry.factor_hash == factor_hash,
            ).limit(1)
        )
        row = result.scalar_one_or_none()
        if row is not None:
            return row

    result = await session.execute(
        select(FactorPoolEntry).where(
            FactorPoolEntry.owner_user_id == owner_user_id,
            FactorPoolEntry.expression_normalized == expression_normalized,
            FactorPoolEntry.universe == universe,
            FactorPoolEntry.holding_period == holding_period,
        ).limit(1)
    )
    return result.scalar_one_or_none()


def _apply_entry_fields(row: FactorPoolEntry, data: dict[str, Any], *, creating: bool) -> None:
    if creating and "expression" not in data:
        raise FactorPoolValidationError("expression is required")

    expression_changed = "expression" in data and data.get("expression") is not None
    if expression_changed:
        row.expression = _required_expression(data.get("expression"))
        row.expression_normalized = normalize_expression(row.expression)

    if creating and not getattr(row, "expression", None):
        row.expression = _required_expression(data.get("expression"))
        row.expression_normalized = normalize_expression(row.expression)

    if "factor_hash" in data and data.get("factor_hash"):
        row.factor_hash = str(data["factor_hash"])
    elif creating or expression_changed or any(key in data for key in ("params", "market", "universe", "holding_period", "validation_stage")):
        row.factor_hash = _resolve_factor_hash(data, row.expression)

    if "pool_status" in data or "status" in data or creating:
        row.pool_status = validate_pool_status(data.get("pool_status", data.get("status", row.pool_status)))

    if "tags" in data or "category" in data or "category_tag" in data or creating:
        category = data.get("category_tag", data.get("category", row.category_tag if not creating else None))
        tags = data.get("tags", row.tags if not creating else None)
        row.tags, row.category_tag = normalize_tags(tags, category)

    scalar_fields = (
        "name",
        "note",
        "main_reason",
        "experiment_id",
        "task_id",
        "market",
        "universe",
        "holding_period",
        "validation_stage",
        "metrics",
        "backtest_summary",
        "params",
        "validation_provenance",
        "report_url",
        "factor_card_path",
        "source",
        "created_by",
    )
    for field in scalar_fields:
        if field in data:
            setattr(row, field, data[field])

    if creating and not row.name:
        row.name = row.expression[:60]
    if creating and not row.market:
        row.market = "a_share"
    if creating and not row.source:
        row.source = "manual"

    row.updated_at = datetime.now(timezone.utc)


def _resolve_factor_hash(data: dict[str, Any], expression: str) -> str:
    if data.get("factor_hash"):
        return str(data["factor_hash"])
    config = dict(data.get("params") or {})
    for key in ("market", "universe", "holding_period", "validation_stage"):
        if data.get(key) is not None:
            config[key] = data[key]
    return compute_factor_hash(expression, config)


def _required_expression(expression: Any) -> str:
    value = str(expression or "").strip()
    if not value:
        raise FactorPoolValidationError("expression is required")
    return value


def _normalize_filter_tags(*, tag: str | None, tags: list[str] | None) -> list[str]:
    values = []
    if tag:
        values.append(tag)
    values.extend(tags or [])
    return [value for value in (normalize_tag(item) for item in values) if value]


def _row_has_all_tags(row: FactorPoolEntry, tags: list[str]) -> bool:
    row_tags = {tag for tag in (normalize_tag(item) for item in (row.tags or [])) if tag}
    return all(tag in row_tags for tag in tags)


def _coerce_uuid(value: str | uuid.UUID, field: str) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise FactorPoolValidationError(f"Invalid {field}: {value}") from exc


def _coerce_int(value: Any, *, default: int, minimum: int, maximum: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed
