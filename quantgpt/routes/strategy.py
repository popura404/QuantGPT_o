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

from ..auth import GUEST_USER_ID, get_optional_user
from ..models import User
from ..strategy.service import (
    diagnose_strategy_payload,
    export_strategy_candidate_payload,
    generate_strategy_report_payload,
    list_strategy_data_fields,
    list_strategy_markets,
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


@router.get("/markets")
def strategy_markets():
    return list_strategy_markets()


@router.get("/data-fields")
def strategy_data_fields(market: str = "a_share"):
    try:
        return list_strategy_data_fields(market)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


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
    user: User | None = Depends(get_optional_user),
):
    client_ip = request.client.host if request.client else "unknown"
    if not check_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")
    if active_task_count() >= MAX_ACTIVE_TASKS:
        raise HTTPException(status_code=503, detail="当前回测任务已满，请稍后再试")

    cleanup_tasks()
    is_guest = user is None
    user_id = str(user.id) if user else GUEST_USER_ID
    body = req.model_dump()
    if is_guest:
        body["spec"] = dict(body["spec"])
        body["spec"]["universe"] = "small_scale"

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
            "is_guest": is_guest,
        }

    thread = threading.Thread(target=_run_strategy_backtest_task, args=(task_id, body, user_id), daemon=True)
    thread.start()
    return {"task_id": task_id, "status": "pending"}


@router.post("/export")
def strategy_export(req: StrategyResultRequest):
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
            "summary_json": report_payload.get("summary_json_path"),
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
