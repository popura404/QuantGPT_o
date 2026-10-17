"""Composite (multi-factor) backtest API routes."""

import logging
from pathlib import Path
from typing import Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user
from ..db import get_db
from ..models import User
from ..research.projects import ProjectAccessError
from ..schemas import fetch_market_data, validate_date_format, validate_universe_value
from ..task_executor import dispatch_durable_task
from ..task_store import (
    CancelledException,
    TaskConflictError,
    check_cancelled,
    snapshot_task,
    submit_durable_task,
    transition_task,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["composite"])

CombinationMethod = Literal["weighted_rank", "weighted_zscore", "equal_weight"]


class FactorItem(BaseModel):
    expression: str = Field(..., description="因子表达式")
    weight: float = Field(1.0, ge=0, le=10, description="权重")
    label: str | None = Field(None, description="因子标签")


class CompositeBacktestRequest(BaseModel):
    factors: list[FactorItem] = Field(..., min_length=2, max_length=10, description="因子列表")
    combination_method: CombinationMethod = Field(
        "weighted_rank",
        description="组合方式: weighted_rank / weighted_zscore / equal_weight",
    )
    universe: str = Field("hs300", description="股票池")
    start_date: str = Field("2023-01-01")
    end_date: str = Field("2025-12-31")
    n_groups: int = Field(5, ge=2, le=20)
    holding_period: int = Field(5, ge=1, le=60)
    benchmark: str = Field("hs300")
    session_id: str | None = Field(None)

    @field_validator("combination_method")
    @classmethod
    def validate_method(cls, v: str) -> CombinationMethod:
        valid = {"weighted_rank", "weighted_zscore", "equal_weight"}
        if v not in valid:
            raise ValueError(f"combination_method 必须是 {valid} 之一")
        return cast(CombinationMethod, v)

    _validate_universe = field_validator("universe")(validate_universe_value)
    _validate_dates = field_validator("start_date", "end_date")(validate_date_format)


class CorrelationRequest(BaseModel):
    factors: list[FactorItem] = Field(..., min_length=2, max_length=10)
    universe: str = Field("hs300")
    start_date: str = Field("2023-01-01")
    end_date: str = Field("2025-12-31")

    _validate_universe = field_validator("universe")(validate_universe_value)
    _validate_dates = field_validator("start_date", "end_date")(validate_date_format)


class AttributionRequest(BaseModel):
    factors: list[FactorItem] = Field(..., min_length=2, max_length=10, description="子因子列表")
    composite_expression: str | None = Field(None, description="组合因子表达式（可选）")
    universe: str = Field("hs300")
    start_date: str = Field("2023-01-01")
    end_date: str = Field("2025-12-31")
    n_groups: int = Field(5, ge=2, le=20)
    holding_period: int = Field(5, ge=1, le=60)

    _validate_universe = field_validator("universe")(validate_universe_value)
    _validate_dates = field_validator("start_date", "end_date")(validate_date_format)


@router.post("/composite-backtest", status_code=202, summary="多因子组合回测")
async def composite_backtest(
    req: CompositeBacktestRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """提交多因子等权/加权组合回测任务，评估因子组合的综合表现。"""
    from ..task_store import (  # noqa: I001
        MAX_ACTIVE_TASKS, active_task_count as _active_task_count,
        check_rate_limit as _check_rate_limit, cleanup_tasks as _cleanup_tasks,
    )

    client_ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")
    if _active_task_count() >= MAX_ACTIVE_TASKS:
        raise HTTPException(status_code=503, detail="当前回测任务已满，请稍后再试")

    _cleanup_tasks()

    user_id = str(user.id)
    try:
        task, created = await submit_durable_task(
            db, actor_id=user_id, task_type="composite", params=req.model_dump(),
            session_id=req.session_id, idempotency_key=request.headers.get("Idempotency-Key"),
        )
    except ProjectAccessError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TaskConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if created:
        await dispatch_durable_task(db, task["task_id"])
    return {"task_id": task["task_id"], "status": task["status"], "reused": not created}


def _run_composite_task(task_id: str, req: CompositeBacktestRequest, user_id: str, *,
                        attempt_id: str | None = None):
    """Background worker for composite backtest."""
    import traceback

    from ..composite import run_composite_backtest
    from ..market_data import fetch_benchmark_returns
    from ..report import generate_report
    from ..task_store import cleanup_reports as _cleanup_reports
    from ..task_store import persist_task_to_db as _persist_task_to_db
    task = snapshot_task(task_id, expected_attempt=attempt_id)
    if not task:
        return

    report_filename = None
    try:
        check_cancelled(task_id, expected_attempt=attempt_id)
        transition_task(task_id, "fetching_data", expected_attempt=attempt_id)
        try:
            market_df, stock_codes = fetch_market_data(req.universe, req.start_date, req.end_date)
        except ValueError as e:
            transition_task(task_id, "failed", expected_attempt=attempt_id, error=str(e))
            return

        # Enrich with fundamental data if any factor uses fundamental vars
        from ..fundamental_data import detect_fundamental_vars, enrich_market_data
        all_fund_vars: set[str] = set()
        for f in req.factors:
            all_fund_vars |= detect_fundamental_vars(f.expression)
        if all_fund_vars:
            check_cancelled(task_id, expected_attempt=attempt_id)
            transition_task(task_id, "fetching_fundamentals", expected_attempt=attempt_id)
            logger.info(f"[{task_id}] composite: enriching with fundamentals: {all_fund_vars}")
            market_df = enrich_market_data(market_df, all_fund_vars, stock_codes, req.start_date, req.end_date)

        check_cancelled(task_id, expected_attempt=attempt_id)
        transition_task(task_id, "backtesting", expected_attempt=attempt_id)
        factors_list = [f.model_dump() for f in req.factors]
        result = run_composite_backtest(
            market_df=market_df,
            factors=factors_list,
            method=req.combination_method,
            n_groups=req.n_groups,
            holding_period=req.holding_period,
        )

        check_cancelled(task_id, expected_attempt=attempt_id)
        transition_task(task_id, "generating_report", expected_attempt=attempt_id)
        bm_returns = None
        try:
            bm_returns = fetch_benchmark_returns(req.benchmark, req.start_date, req.end_date)
        except Exception:
            pass

        user_report_dir = Path(__file__).resolve().parent.parent.parent / "reports" / user_id
        user_report_dir.mkdir(parents=True, exist_ok=True)

        report_result = generate_report(
            result["strategy_returns"],
            benchmark_returns=bm_returns,
            title="Composite Factor Backtest",
            output_dir=str(user_report_dir),
        )
        report_filename = Path(report_result["report_path"]).name

        composite_expr = result.get("composite_expression", "")
        task_result = {
            "report_url": f"/api/v1/reports/{report_filename}",
            "metrics": report_result["metrics"],
            "backtest_summary": {
                "long_short_sharpe": result["long_short_sharpe"],
                "long_short_annual": result.get("long_short_annual", 0),
                "top_group_sharpe": result.get("top_group_sharpe", 0),
                "monotonicity_score": result["monotonicity_score"],
                "spread": result["spread"],
                "group_returns": result["group_returns"],
                "ic_mean": result.get("ic_mean", 0),
                "rank_ic_mean": result.get("rank_ic_mean", 0),
                "ic_ir": result.get("ic_ir", 0),
                "ic_win_rate": result.get("ic_win_rate", 0),
                "turnover": result.get("turnover", 0),
            },
            "correlation": result.get("correlation"),
            "params": {
                "expression": composite_expr,
                "factors": factors_list,
                "combination_method": req.combination_method,
                "universe": req.universe,
                "start_date": req.start_date,
                "end_date": req.end_date,
                "n_groups": req.n_groups,
                "holding_period": req.holding_period,
                "benchmark": req.benchmark,
                "stock_count": len(stock_codes),
            },
            "llm": {
                "prompt": f"多因子组合回测: {len(factors_list)}个因子",
                "generated_expression": composite_expr,
            },
        }
        check_cancelled(task_id, expected_attempt=attempt_id)
        transition_task(task_id, "completed", expected_attempt=attempt_id,
                        expression=composite_expr, result=task_result)
        _cleanup_reports(user_id)

    except CancelledException:
        transition_task(task_id, "cancelled", expected_attempt=attempt_id)
        report_filename = None
    except Exception as e:
        logger.error(f"[{task_id}] composite backtest failed: {traceback.format_exc()}")
        transition_task(task_id, "failed", expected_attempt=attempt_id, error=f"多因子组合回测失败: {str(e)}")
    finally:
        task = snapshot_task(task_id, expected_attempt=attempt_id)
        try:
            if task is not None:
                _persist_task_to_db(task_id, user_id, task, report_filename)
        except Exception as e:
            logger.error(f"[{task_id}] DB persist error: {e}")


@router.post("/factor-correlation")
def factor_correlation(
    req: CorrelationRequest,
    user: User = Depends(get_current_user),
):
    """计算多因子间的相关性矩阵。"""
    from ..composite import compute_factor_correlation

    try:
        market_df, stock_codes = fetch_market_data(req.universe, req.start_date, req.end_date)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Enrich with fundamentals if needed
    from ..fundamental_data import detect_fundamental_vars, enrich_market_data
    all_fund_vars: set[str] = set()
    for f in req.factors:
        all_fund_vars |= detect_fundamental_vars(f.expression)
    if all_fund_vars:
        market_df = enrich_market_data(market_df, all_fund_vars, stock_codes, req.start_date, req.end_date)

    factors_list = [f.model_dump() for f in req.factors]
    correlation = compute_factor_correlation(market_df, factors_list)
    return correlation


@router.post("/factor-attribution")
def factor_attribution(
    req: AttributionRequest,
    user: User = Depends(get_current_user),
):
    """因子归因分解：分析各子因子对组合的贡献度。"""
    from ..attribution import compute_factor_attribution

    try:
        market_df, stock_codes = fetch_market_data(req.universe, req.start_date, req.end_date)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Enrich with fundamentals if needed
    from ..fundamental_data import detect_fundamental_vars, enrich_market_data
    all_fund_vars: set[str] = set()
    for f in req.factors:
        all_fund_vars |= detect_fundamental_vars(f.expression)
    if req.composite_expression:
        all_fund_vars |= detect_fundamental_vars(req.composite_expression)
    if all_fund_vars:
        market_df = enrich_market_data(market_df, all_fund_vars, stock_codes, req.start_date, req.end_date)

    factors_list = [f.model_dump() for f in req.factors]
    result = compute_factor_attribution(
        market_df=market_df,
        sub_factors=factors_list,
        composite_expression=req.composite_expression,
        n_groups=req.n_groups,
        holding_period=req.holding_period,
    )
    return result
