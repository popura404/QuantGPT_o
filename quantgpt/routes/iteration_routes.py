"""Iteration optimization routes and background worker."""

import asyncio
import logging
import traceback

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user
from ..db import get_db
from ..iteration import compute_factor_score, generate_iteration_candidates
from ..market_data import MarketDataFetcher, get_universe
from ..models import Task as TaskModel
from ..models import User
from ..research.projects import ProjectAccessError, require_project_member
from ..search_ledger import summarize_attempts
from ..task_executor import dispatch_durable_task
from ..task_store import (
    MAX_ACTIVE_TASKS,
    REPORT_DIR,
    SAFE_FILENAME_RE,
    CancelledException,
    TaskConflictError,
    active_task_count,
    check_cancelled,
    check_rate_limit,
    cleanup_tasks,
    create_report_ticket,
    load_durable_task,
    persist_task_to_db,
    snapshot_task,
    submit_durable_task,
    tasks,
    tasks_lock,
    transition_task,
    validate_report_ticket,
)
from ..validation.promotion import BOUNDARY_CANDIDATE, evaluate_promotion_provenance

logger = logging.getLogger(__name__)

router = APIRouter()

REPORT_CSP = (
    "default-src 'self'; "
    "script-src 'none'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "frame-ancestors 'self'"
)


class IterateRequest(BaseModel):
    n_candidates: int = Field(5, ge=1, le=10, description="候选因子数量")
    run_rolling_validation: bool = Field(False, description="是否运行滚动验证")
    direction: str | None = Field(None, description="迭代方向提示，如'加入量价信息'、'增加低波暴露'")


class SelectCandidateRequest(BaseModel):
    candidate_index: int = Field(..., ge=0, description="候选因子索引")


def _run_iteration_task(task_id: str, parent_task_id: str, user_id: str, n_candidates: int,
                        direction: str | None = None, *, attempt_id: str | None = None):
    task = snapshot_task(task_id, expected_attempt=attempt_id)
    if not task:
        return

    try:
        check_cancelled(task_id, expected_attempt=attempt_id)
        task.update(candidates=[], search_attempts=[])
        parent_task = (task.get("params") or {}).get("parent_snapshot") or snapshot_task(parent_task_id)
        if parent_task and parent_task.get("status") == "completed":
            parent_result = parent_task.get("result", {})
            parent_expression = parent_task.get("expression", "")
            parent_params = parent_result.get("params", {})
        else:
            async def _fetch_parent():
                from ..db import _get_session_factory
                factory = _get_session_factory()
                async with factory() as session:
                    return await load_durable_task(session, parent_task_id, user_id, write=True)

            db_parent = None
            from .. import task_store
            _loop = task_store.main_loop
            if _loop and _loop.is_running():
                future = asyncio.run_coroutine_threadsafe(_fetch_parent(), _loop)
                try:
                    db_parent = future.result(timeout=10)
                except Exception as e:
                    logger.error(f"[{task_id}] fetch parent from DB failed: {e}")

            if not db_parent or db_parent["status"] != "completed":
                transition_task(task_id, "failed", expected_attempt=attempt_id, error="父任务未完成或不存在")
                return

            parent_result = db_parent["result"] or {}
            parent_expression = db_parent["expression"] or ""
            parent_params = parent_result.get("params", {})

        if not parent_expression:
            transition_task(task_id, "failed", expected_attempt=attempt_id, error="父任务缺少表达式")
            return

        transition_task(task_id, "iterating", expected_attempt=attempt_id, expression=parent_expression,
                        parent_task_id=parent_task_id, candidates=[], candidates_done=0,
                        candidates_total=n_candidates, search_attempts=[], search_summary={})

        stock_codes = get_universe(
            parent_params.get("universe", "hs300"),
            date=parent_params.get("start_date", "2023-01-01"),
        )
        fetcher = MarketDataFetcher()
        market_df = fetcher.fetch_stocks(
            stock_codes,
            parent_params.get("start_date", "2023-01-01"),
            parent_params.get("end_date", "2025-12-31"),
        )
        if market_df is None or len(market_df) == 0:
            transition_task(task_id, "failed", expected_attempt=attempt_id, error="无法获取行情数据")
            return

        from ..fundamental_data import detect_fundamental_vars, enrich_market_data
        fund_vars = detect_fundamental_vars(parent_expression)
        if fund_vars:
            logger.info(f"[{task_id}] iteration: enriching with fundamentals: {fund_vars}")
            sd = parent_params.get("start_date", "2023-01-01")
            ed = parent_params.get("end_date", "2025-12-31")
            market_df = enrich_market_data(market_df, fund_vars, stock_codes, sd, ed)

        parent_backtest_summary = parent_result.get("backtest_summary", {})
        parent_report_metrics = parent_result.get("metrics", {})
        parent_scoring = compute_factor_score(parent_backtest_summary, parent_report_metrics)

        parent_metrics = {
            "backtest_summary": parent_backtest_summary,
            "report_metrics": parent_report_metrics,
        }

        def on_attempt(attempt):
            check_cancelled(task_id, expected_attempt=attempt_id)
            existing = task.setdefault("search_attempts", [])
            for idx, item in enumerate(existing):
                if item.get("id") == attempt.get("id"):
                    existing[idx] = attempt
                    break
            else:
                existing.append(attempt)
            task["search_summary"] = summarize_attempts(existing)
            transition_task(task_id, expected_attempt=attempt_id, search_attempts=list(existing),
                            search_summary=task["search_summary"])

        def on_progress(done_count, candidate_result):
            check_cancelled(task_id, expected_attempt=attempt_id)
            task["candidates_done"] = done_count
            task["candidates"].append(candidate_result)
            transition_task(task_id, expected_attempt=attempt_id, candidates_done=done_count,
                            candidates=list(task["candidates"]))
            report_filename = candidate_result.get("report_filename")
            if candidate_result.get("status") == "success" and report_filename:
                current = snapshot_task(task_id, expected_attempt=attempt_id)
                if current is not None:
                    # Publish the report in the guarded task transaction so
                    # cancellation, revoked access and stale attempts also fence it.
                    persist_task_to_db(task_id, user_id, current, report_filename)

        check_cancelled(task_id, expected_attempt=attempt_id)
        candidates = generate_iteration_candidates(
            parent_expression=parent_expression,
            parent_metrics=parent_metrics,
            parent_score=parent_scoring["score"],
            parent_grade=parent_scoring["grade"],
            params=parent_params,
            market_df=market_df,
            user_id=user_id,
            n_candidates=n_candidates,
            max_concurrent=50,
            on_progress=on_progress,
            on_attempt=on_attempt,
            task_id=task_id,
            parent_task_id=parent_task_id,
            direction=direction,
        )

        check_cancelled(task_id, expected_attempt=attempt_id)
        summary = summarize_attempts(task.get("search_attempts", []))
        result = {
            "parent_task_id": parent_task_id,
            "parent_expression": parent_expression,
            "parent_score": parent_scoring["score"],
            "parent_grade": parent_scoring["grade"],
            "candidates": candidates,
            "search_attempts": task.get("search_attempts", []),
            "search_summary": summary,
        }
        transition_task(task_id, "iteration_completed", expected_attempt=attempt_id, result=result,
                        candidates=candidates, candidates_done=len(candidates), search_summary=summary)
        logger.info(f"[{task_id}] iteration completed: {len(candidates)} candidates")

    except CancelledException:
        transition_task(task_id, "cancelled", expected_attempt=attempt_id)
    except Exception as e:
        logger.error(f"[{task_id}] iteration failed: {traceback.format_exc()}")
        transition_task(task_id, "failed", expected_attempt=attempt_id, error=f"迭代过程中发生错误: {str(e)}")
    finally:
        current_task = snapshot_task(task_id, expected_attempt=attempt_id)
        if current_task is not None and not current_task.get("is_guest"):
            try:
                persist_task_to_db(task_id, user_id, current_task)
            except Exception as e:
                logger.error(f"[{task_id}] DB persist error: {e}")


@router.post("/api/v1/tasks/{task_id}/iterate", status_code=202, summary="提交迭代优化任务")
async def iterate_task(
    task_id: str,
    req: IterateRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    client_ip = request.client.host if request.client else "unknown"
    if not check_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")

    user_id = str(user.id)

    try:
        parent_task = await load_durable_task(db, task_id, user_id, write=True)
    except ProjectAccessError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc
    parent_task = parent_task or snapshot_task(task_id)
    if not parent_task or (not parent_task.get("project_id") and parent_task.get("user_id") != user_id):
        raise HTTPException(status_code=404, detail="Task not found")
    if parent_task.get("status") != "completed":
        raise HTTPException(status_code=400, detail="只能对已完成的任务进行迭代优化")
    parent_params = (parent_task.get("result") or {}).get("params", {})
    parent_expression = parent_task.get("expression")

    if active_task_count() >= MAX_ACTIVE_TASKS:
        raise HTTPException(status_code=503, detail="当前任务已满，请稍后再试")

    cleanup_tasks()

    # Freeze the authorized parent input before acceptance; restart never depends
    # on an expiring in-memory parent or on changing legacy result dictionaries.
    parent_snapshot = {"status": "completed", "expression": parent_expression,
                       "result": parent_task.get("result") or {}}
    try:
        task, created = await submit_durable_task(db, actor_id=user_id, task_type="iteration",
            params={**parent_params, "parent_task_id": task_id, "parent_snapshot": parent_snapshot,
                    **req.model_dump()}, expression=parent_expression,
            project_id=parent_task.get("project_id"),
            session_id=parent_task.get("session_id") if parent_task.get("user_id") == user_id else None,
            idempotency_key=request.headers.get("Idempotency-Key"))
    except TaskConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ProjectAccessError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if created:
        await dispatch_durable_task(db, task["task_id"])
    return {"task_id": task["task_id"], "status": task["status"], "reused": not created}


@router.post("/api/v1/tasks/{task_id}/select_candidate", summary="选择迭代候选因子")
async def select_candidate(
    task_id: str,
    req: SelectCandidateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user_id = str(user.id)

    try:
        task = await load_durable_task(db, task_id, user_id, write=True)
    except ProjectAccessError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc
    persisted = task is not None
    task = task or snapshot_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if not task.get("project_id") and task.get("user_id") != user_id:
        raise HTTPException(status_code=404, detail="Task not found")
    if not persisted and task.get("project_id"):
        try:
            await require_project_member(db, user_id, task["project_id"], write=True)
        except ProjectAccessError as exc:
            raise HTTPException(status_code=404, detail="Task not found") from exc
    if task.get("status") != "iteration_completed":
        raise HTTPException(status_code=400, detail="迭代任务尚未完成")

    candidates = task.get("candidates", [])
    if req.candidate_index >= len(candidates):
        raise HTTPException(status_code=400, detail="候选索引超出范围")

    candidate = candidates[req.candidate_index]
    if candidate.get("status") != "success":
        raise HTTPException(status_code=400, detail="该候选因子回测失败，无法选择")
    promotion_gate = evaluate_promotion_provenance(
        candidate.get("validation_provenance"),
        BOUNDARY_CANDIDATE,
    )
    if not promotion_gate["allowed"]:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "CANDIDATE_PROMOTION_BLOCKED",
                "message": "候选因子必须先通过 train/validation/test、rolling window、placebo、time-shift 和 data quality gate。",
                "promotion_gate": promotion_gate,
            },
        )

    task["selected_candidate_index"] = req.candidate_index
    if persisted:
        record = await db.get(TaskModel, task_id)
        assert record is not None
        record.result = {**(record.result or {}), "selected_candidate_index": req.candidate_index}
        await db.commit()
    with tasks_lock:
        if task_id in tasks:
            tasks[task_id]["selected_candidate_index"] = req.candidate_index

    return {
        "task_id": task_id,
        "selected_index": req.candidate_index,
        "expression": candidate.get("expression"),
        "score": candidate.get("score"),
        "grade": candidate.get("grade"),
        "report_url": candidate.get("report_url"),
        "report_metrics": candidate.get("report_metrics"),
        "backtest_summary": candidate.get("backtest_summary"),
    }


@router.get("/api/v1/reports/{filename}", summary="下载 HTML 回测报告")
async def get_report(
    filename: str,
    request: Request,
    ticket: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    if not SAFE_FILENAME_RE.match(filename):
        raise HTTPException(status_code=400, detail="Invalid filename")

    if ticket:
        user_id = validate_report_ticket(ticket, filename)
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid or expired report ticket")
    else:
        user = await get_current_user(request, db)
        user_id = str(user.id)

    user_report_dir = REPORT_DIR / user_id
    file_path = (user_report_dir / filename).resolve()

    if not file_path.is_relative_to(user_report_dir.resolve()):
        raise HTTPException(status_code=400, detail="Invalid path")

    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Report not found")

    return FileResponse(
        str(file_path),
        media_type="text/html",
        headers={"Content-Security-Policy": REPORT_CSP},
    )


@router.post("/api/v1/reports/{filename}/ticket", summary="创建 HTML 报告下载票据")
async def create_report_download_ticket(
    filename: str,
    user: User = Depends(get_current_user),
):
    if not SAFE_FILENAME_RE.match(filename):
        raise HTTPException(status_code=400, detail="Invalid filename")
    return {"ticket": create_report_ticket(filename, str(user.id))}
