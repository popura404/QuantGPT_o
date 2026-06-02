"""Factor pool routes with tag/category/status filtering."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user
from ..db import get_db
from ..factor_pool import (
    FactorPoolNotFoundError,
    FactorPoolValidationError,
    delete_factor_pool_entry,
    factor_pool_entry_to_dict,
    get_factor_pool_entry,
    list_factor_pool_entries,
    list_factor_pool_tags,
    save_factor_pool_entry,
    update_factor_pool_entry,
)
from ..models import User

router = APIRouter(prefix="/api/v1/factor-pool", tags=["factor-pool"])


class SaveFactorPoolRequest(BaseModel):
    entry_id: str | None = None
    expression: str
    name: str | None = None
    note: str | None = None
    main_reason: str | None = None
    tags: list[str] = Field(default_factory=list)
    category: str | None = None
    category_tag: str | None = None
    pool_status: str | None = None
    status: str | None = None
    factor_hash: str | None = None
    experiment_id: str | None = None
    task_id: str | None = None
    market: str | None = "a_share"
    universe: str | None = None
    holding_period: int | None = None
    validation_stage: str | None = None
    metrics: dict[str, Any] | None = None
    backtest_summary: dict[str, Any] | None = None
    params: dict[str, Any] | None = None
    validation_provenance: dict[str, Any] | None = None
    report_url: str | None = None
    factor_card_path: str | None = None
    source: str | None = "manual"
    created_by: str | None = "api"


class UpdateFactorPoolRequest(BaseModel):
    expression: str | None = None
    name: str | None = None
    note: str | None = None
    main_reason: str | None = None
    tags: list[str] | None = None
    category: str | None = None
    category_tag: str | None = None
    pool_status: str | None = None
    status: str | None = None
    factor_hash: str | None = None
    experiment_id: str | None = None
    task_id: str | None = None
    market: str | None = None
    universe: str | None = None
    holding_period: int | None = None
    validation_stage: str | None = None
    metrics: dict[str, Any] | None = None
    backtest_summary: dict[str, Any] | None = None
    params: dict[str, Any] | None = None
    validation_provenance: dict[str, Any] | None = None
    report_url: str | None = None
    factor_card_path: str | None = None
    source: str | None = None
    created_by: str | None = None


@router.post("", summary="保存或更新因子池条目")
async def save_factor_pool(
    req: SaveFactorPoolRequest,
    response: Response,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    payload = _model_to_dict(req, exclude_unset=True)
    entry_id = payload.pop("entry_id", None)
    try:
        entry, created = await save_factor_pool_entry(
            db,
            owner_user_id=user.id,
            entry_id=entry_id,
            data=payload,
        )
        await db.commit()
        await db.refresh(entry)
    except FactorPoolValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FactorPoolNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    response.status_code = 201 if created else 200
    return {"entry": factor_pool_entry_to_dict(entry), "created": created}


@router.get("", summary="查询因子池列表")
async def list_factor_pool(
    pool_status: str | None = None,
    status: str | None = None,
    category: str | None = None,
    tag: str | None = None,
    tags: list[str] | None = Query(default=None),
    universe: str | None = None,
    market: str | None = None,
    factor_hash: str | None = None,
    experiment_id: str | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        entries, total = await list_factor_pool_entries(
            db,
            owner_user_id=user.id,
            pool_status=pool_status or status,
            category=category,
            tag=tag,
            tags=tags,
            universe=universe,
            market=market,
            factor_hash=factor_hash,
            experiment_id=experiment_id,
            q=q,
            limit=limit,
            offset=offset,
        )
    except FactorPoolValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "entries": [factor_pool_entry_to_dict(entry) for entry in entries],
        "total": total,
        "limit": max(1, min(int(limit), 200)),
        "offset": max(0, int(offset)),
    }


@router.get("/tags", summary="查询因子池 tag/category facets")
async def list_factor_pool_tag_facets(
    pool_status: str | None = None,
    status: str | None = None,
    universe: str | None = None,
    market: str | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await list_factor_pool_tags(
            db,
            owner_user_id=user.id,
            pool_status=pool_status or status,
            universe=universe,
            market=market,
        )
    except FactorPoolValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{entry_id}", summary="查询单个因子池条目")
async def get_factor_pool(
    entry_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        entry = await get_factor_pool_entry(db, owner_user_id=user.id, entry_id=entry_id)
    except FactorPoolValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FactorPoolNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return factor_pool_entry_to_dict(entry)


@router.patch("/{entry_id}", summary="更新因子池条目")
async def update_factor_pool(
    entry_id: str,
    req: UpdateFactorPoolRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    payload = _model_to_dict(req, exclude_unset=True)
    try:
        entry = await update_factor_pool_entry(db, owner_user_id=user.id, entry_id=entry_id, data=payload)
        await db.commit()
        await db.refresh(entry)
    except FactorPoolValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FactorPoolNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return factor_pool_entry_to_dict(entry)


@router.delete("/{entry_id}", status_code=204, summary="删除因子池条目")
async def delete_factor_pool(
    entry_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        await delete_factor_pool_entry(db, owner_user_id=user.id, entry_id=entry_id)
        await db.commit()
    except FactorPoolValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FactorPoolNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _model_to_dict(model: BaseModel, *, exclude_unset: bool = False) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump(exclude_unset=exclude_unset)
    return model.dict(exclude_unset=exclude_unset)
