"""Strategy framework REST routes."""

from __future__ import annotations

import logging
import threading
import time
import traceback
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user
from ..db import get_db
from ..models import Strategy as StrategyModel
from ..models import StrategyRun as StrategyRunModel
from ..models import User
from ..strategy.service import (
    diagnose_strategy_payload,
    export_strategy_candidate_payload,
    generate_strategy_report_payload,
    get_strategy_template_payload,
    instantiate_strategy_template_payload,
    list_strategy_data_fields,
    list_strategy_markets,
    list_strategy_templates_payload,
    optimize_candidate_weights_payload,
    run_strategy_anti_overfit_payload,
    run_strategy_backtest_payload,
    run_strategy_rolling_validation_payload,
    validate_strategy_payload,
)
from ..task_store import (
    MAX_ACTIVE_TASKS,
    active_task_count,
    check_rate_limit,
    cleanup_tasks,
    persist_task_to_db,
    tasks,
    tasks_lock,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/strategy", tags=["strategy"])


class StrategyValidateRequest(BaseModel):
    spec: dict


class StrategyBacktestRequestBody(BaseModel):
    spec: dict
    start_date: str = Field(..., description="YYYY-MM-DD")
    end_date: str = Field(..., description="YYYY-MM-DD")
    benchmark: str = "hs300"
    universe_date: str | None = None
    rebalance_anchor: str | None = None


class StrategyResultRequest(BaseModel):
    result: dict


class StrategyRollingValidationRequest(BaseModel):
    result: dict
    windows: int = Field(3, ge=1, le=12)


class StrategySaveRequest(BaseModel):
    spec: dict
    name: str | None = Field(None, max_length=120)
    tags: list[str] = Field(default_factory=list)


class StrategyRunSaveRequest(BaseModel):
    result: dict
    strategy_id: str | None = None
    task_id: str | None = Field(None, max_length=12)
    report_url: str | None = None
    summary_json: str | None = None
    signal_export: dict | None = None


class StrategyTemplateInstantiateRequest(BaseModel):
    overrides: dict | None = None


class StrategyOptimizeRequest(BaseModel):
    signals: list[dict]
    spec: dict


@router.get("/markets")
def strategy_markets():
    return list_strategy_markets()


@router.get("/data-fields")
def strategy_data_fields(market: str = "a_share"):
    try:
        return list_strategy_data_fields(market)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/templates")
def strategy_templates():
    return list_strategy_templates_payload()


@router.get("/templates/{template_id}")
def strategy_template(template_id: str):
    try:
        return get_strategy_template_payload(template_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/templates/{template_id}/instantiate")
def strategy_template_instantiate(template_id: str, req: StrategyTemplateInstantiateRequest):
    try:
        return instantiate_strategy_template_payload(template_id, overrides=req.overrides)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/validate")
def strategy_validate(req: StrategyValidateRequest):
    result = validate_strategy_payload(req.spec)
    if not result["is_valid"]:
        raise HTTPException(status_code=400, detail=result)
    return result


@router.post("/backtest", status_code=202)
async def strategy_backtest(
    req: StrategyBacktestRequestBody,
    request: Request,
    user: User = Depends(get_current_user),
):
    client_ip = request.client.host if request.client else "unknown"
    if not check_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")
    if active_task_count() >= MAX_ACTIVE_TASKS:
        raise HTTPException(status_code=503, detail="当前回测任务已满，请稍后再试")

    cleanup_tasks()
    user_id = str(user.id)
    body = req.model_dump()

    validation = validate_strategy_payload(body["spec"])
    if not validation["is_valid"]:
        raise HTTPException(status_code=400, detail=validation)

    task_id = uuid.uuid4().hex[:12]
    with tasks_lock:
        tasks[task_id] = {
            "task_id": task_id,
            "user_id": user_id,
            "status": "pending",
            "cancelled": False,
            "task_type": "strategy_backtest",
            "params": body,
            "created_at": time.time(),
            "is_guest": False,
        }

    thread = threading.Thread(target=_run_strategy_backtest_task, args=(task_id, body, user_id), daemon=True)
    thread.start()
    return {"task_id": task_id, "status": "pending"}


@router.post("/export")
def strategy_export(req: StrategyResultRequest, user: User = Depends(get_current_user)):
    del user
    try:
        return export_strategy_candidate_payload(req.result)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/diagnose")
def strategy_diagnose(req: StrategyResultRequest):
    try:
        return diagnose_strategy_payload(req.result)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/anti-overfit")
def strategy_anti_overfit(req: StrategyResultRequest):
    try:
        return run_strategy_anti_overfit_payload(req.result)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/rolling-validation")
def strategy_rolling_validation(req: StrategyRollingValidationRequest):
    try:
        return run_strategy_rolling_validation_payload(req.result, windows=req.windows)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/optimize")
def strategy_optimize(req: StrategyOptimizeRequest):
    try:
        return optimize_candidate_weights_payload(req.signals, req.spec)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/specs", status_code=201)
async def save_strategy_spec(
    req: StrategySaveRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    validation = validate_strategy_payload(req.spec)
    if not validation["is_valid"]:
        raise HTTPException(status_code=400, detail=validation)
    spec = validation["spec"]
    strategy = StrategyModel(
        id=uuid.uuid4(),
        user_id=user.id,
        name=req.name or spec["name"],
        schema_version=spec["schema_version"],
        market=spec["market"],
        universe=spec["universe"],
        spec=spec,
        tags=req.tags,
    )
    db.add(strategy)
    await db.commit()
    await db.refresh(strategy)
    return _strategy_model_to_dict(strategy)


@router.get("/specs")
async def list_strategy_specs(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(StrategyModel)
        .where(StrategyModel.user_id == user.id)
        .order_by(desc(StrategyModel.updated_at))
    )
    return {"strategies": [_strategy_model_to_dict(strategy) for strategy in result.scalars().all()]}


@router.get("/specs/{strategy_id}")
async def get_strategy_spec(
    strategy_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    strategy = await _get_owned_strategy(db, user, strategy_id)
    return _strategy_model_to_dict(strategy)


@router.post("/runs", status_code=201)
async def save_strategy_run(
    req: StrategyRunSaveRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    strategy_uuid = None
    if req.strategy_id:
        strategy = await _get_owned_strategy(db, user, req.strategy_id)
        strategy_uuid = strategy.id
    run = StrategyRunModel(
        id=uuid.uuid4(),
        strategy_id=strategy_uuid,
        user_id=user.id,
        task_id=req.task_id,
        result=req.result,
        report_url=req.report_url or req.result.get("report_url"),
        summary_json=req.summary_json or req.result.get("summary_json"),
        signal_export=req.signal_export,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return _strategy_run_model_to_dict(run)


@router.get("/runs")
async def list_strategy_runs(
    strategy_id: str | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(StrategyRunModel).where(StrategyRunModel.user_id == user.id)
    if strategy_id:
        strategy = await _get_owned_strategy(db, user, strategy_id)
        stmt = stmt.where(StrategyRunModel.strategy_id == strategy.id)
    result = await db.execute(stmt.order_by(desc(StrategyRunModel.created_at)))
    return {"runs": [_strategy_run_model_to_dict(run) for run in result.scalars().all()]}


def _run_strategy_backtest_task(task_id: str, request_data: dict, user_id: str) -> None:
    task = tasks.get(task_id)
    if not task:
        return
    report_filename = None
    try:
        task["status"] = "backtesting"
        result_payload = _execute_strategy_backtest(request_data, user_id)
        task["status"] = "generating_report"
        report_payload = _execute_strategy_report(result_payload, user_id)
        report_filename = Path(report_payload["report_path"]).name
        task["status"] = "completed"
        task["result"] = {
            "strategy_result": result_payload,
            "strategy_score": result_payload.get("strategy_score"),
            "report_url": f"/api/v1/reports/{report_filename}",
        }
    except Exception as exc:
        logger.error(f"[{task_id}] strategy backtest failed: {traceback.format_exc()}")
        task["status"] = "failed"
        task["error"] = str(exc)
    finally:
        task["completed_at"] = time.time()
        if not task.get("is_guest"):
            try:
                persist_task_to_db(task_id, user_id, task, report_filename)
            except Exception as exc:
                logger.error(f"[{task_id}] DB persist error: {exc}")


def _execute_strategy_backtest(request_data: dict, user_id: str) -> dict:
    del user_id
    return run_strategy_backtest_payload(request_data)


def _execute_strategy_report(result_payload: dict, user_id: str) -> dict:
    report_dir = Path(__file__).resolve().parent.parent.parent / "reports" / user_id
    report_dir.mkdir(parents=True, exist_ok=True)
    return generate_strategy_report_payload(result_payload, output_dir=str(report_dir))


async def _get_owned_strategy(db: AsyncSession, user: User, strategy_id: str) -> StrategyModel:
    try:
        strategy_uuid = uuid.UUID(strategy_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid strategy_id") from exc
    result = await db.execute(
        select(StrategyModel).where(StrategyModel.id == strategy_uuid, StrategyModel.user_id == user.id)
    )
    strategy = result.scalar_one_or_none()
    if not strategy:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return strategy


def _strategy_model_to_dict(strategy: StrategyModel) -> dict:
    return {
        "id": str(strategy.id),
        "name": strategy.name,
        "schema_version": strategy.schema_version,
        "market": strategy.market,
        "universe": strategy.universe,
        "spec": strategy.spec,
        "tags": strategy.tags or [],
        "status": strategy.status,
        "created_at": strategy.created_at.isoformat() if strategy.created_at else None,
        "updated_at": strategy.updated_at.isoformat() if strategy.updated_at else None,
    }


def _strategy_run_model_to_dict(run: StrategyRunModel) -> dict:
    return {
        "id": str(run.id),
        "strategy_id": str(run.strategy_id) if run.strategy_id else None,
        "task_id": run.task_id,
        "result": run.result,
        "report_url": run.report_url,
        "summary_json": run.summary_json,
        "signal_export": run.signal_export,
        "created_at": run.created_at.isoformat() if run.created_at else None,
    }
