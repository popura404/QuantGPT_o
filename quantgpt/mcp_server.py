"""FastMCP server for factor backtesting.

Provides tools for Agent-driven backtest workflow:
- list_operators: Show available factor expression operators
- list_universes: Show available stock universes
- validate_expression: Check expression syntax
- run_backtest: Execute full backtest pipeline
- score_factor: Compute composite factor quality score
- diagnose_factor: Diagnose factor issues and suggest mutations
- run_anti_overfit: Run anti-overfit detection
- run_rolling_validation: Walk-forward rolling validation
"""

import asyncio
import json
import logging
import os
import sys
import time
import traceback
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import date
from typing import Awaitable, Callable, Literal

import pandas as pd
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .data_quality import DataQualityConfig, run_data_quality_gate
from .data_snapshots import ensure_market_frame_snapshot, persist_data_snapshot, snapshot_result_fields
from .experiment_ledger import (
    get_experiment as _ledger_get_experiment,
)
from .experiment_ledger import (
    list_experiments as _ledger_list_experiments,
)
from .experiment_ledger import (
    record_experiment as _ledger_record_experiment,
)
from .experiment_ledger import (
    record_experiment_artifact as _ledger_record_experiment_artifact,
)
from .experiment_ledger import (
    record_experiment_result as _ledger_record_experiment_result,
)
from .experiment_ledger import (
    record_export_event as _ledger_record_export_event,
)
from .experiment_ledger import (
    record_promotion_event as _ledger_record_promotion_event,
)
from .experiment_ledger import (
    summarize_trial_counts as _ledger_summarize_trial_counts,
)
from .experiment_ledger import (
    transition_status as _ledger_transition_status,
)
from .expression_parser import __doc__ as _expr_module_doc
from .expression_parser import parse_expression
from .factor_pool import (
    MCP_SYSTEM_USER_ID,
    FactorPoolError,
    ensure_mcp_system_user,
    factor_pool_entry_to_dict,
)
from .factor_pool import (
    delete_factor_pool_entry as _pool_delete_factor_pool_entry,
)
from .factor_pool import (
    get_factor_pool_entry as _pool_get_factor_pool_entry,
)
from .factor_pool import (
    list_factor_pool_entries as _pool_list_factor_pool_entries,
)
from .factor_pool import (
    list_factor_pool_tags as _pool_list_factor_pool_tags,
)
from .factor_pool import (
    save_factor_pool_entry as _pool_save_factor_pool_entry,
)
from .factor_pool import (
    update_factor_pool_entry as _pool_update_factor_pool_entry,
)
from .factor_values import compute_factor_values_payload as _compute_factor_values_payload
from .factor_values import validate_factor_values_request as _validate_factor_values_request
from .fundamental_data import ALL_FUNDAMENTAL_NAMES
from .market_data import (
    BENCHMARK_CODES,
    UNIVERSES,
    MarketDataFetcher,
    describe_stock_cache,
    fetch_benchmark_returns,
    get_universe,
    list_universe_cache_months,
    plan_stock_cache_fetch,
    read_cached_universe,
    universe_cache_path,
)
from .mcp_task_helper import (
    complete_mcp_task,
    force_mcp_task_id,
    get_mcp_task_status_payload,
    request_mcp_task_cancel,
    reset_forced_mcp_task_id,
    start_mcp_task,
    update_mcp_task_progress,
    update_mcp_task_progress_sync,
)
from .report import generate_report
from .statistics.factor_similarity import factor_similarity_report as _factor_similarity_report
from .statistics.multiple_testing import multiple_testing_report as _multiple_testing_report
from .strategy.service import (
    diagnose_strategy_payload as _diagnose_strategy_payload,
)
from .strategy.service import (
    dumps as _strategy_dumps,
)
from .strategy.service import (
    export_strategy_candidate_payload as _export_strategy_candidate_payload,
)
from .strategy.service import (
    generate_strategy_report_payload as _generate_strategy_report_payload,
)
from .strategy.service import (
    get_strategy_template_payload as _get_strategy_template_payload,
)
from .strategy.service import (
    instantiate_strategy_template_payload as _instantiate_strategy_template_payload,
)
from .strategy.service import (
    list_strategy_data_fields as _list_strategy_data_fields,
)
from .strategy.service import (
    list_strategy_markets as _list_strategy_markets,
)
from .strategy.service import (
    list_strategy_templates_payload as _list_strategy_templates_payload,
)
from .strategy.service import (
    optimize_candidate_weights_payload as _optimize_candidate_weights_payload,
)
from .strategy.service import (
    run_strategy_anti_overfit_payload as _run_strategy_anti_overfit_payload,
)
from .strategy.service import (
    run_strategy_backtest_payload as _run_strategy_backtest_payload,
)
from .strategy.service import (
    run_strategy_rolling_validation_payload as _run_strategy_rolling_validation_payload,
)
from .strategy.service import (
    score_strategy_payload as _score_strategy_payload,
)
from .strategy.service import (
    validate_strategy_payload as _validate_strategy_payload,
)
from .task_executor import _run_backtest_in_process, _run_oos_backtest_in_process, get_executor
from .task_store import CancelledException, check_cancelled
from .validation.oos_backtest import to_public_oos_result
from .validation.oos_score import (
    FINAL_TEST_NOT_RUN,
    compute_oos_score,
    compute_oos_selection_score,
    withhold_final_test,
)
from .validation.policy import (
    BIASED_DIRECTION_MODE,
    FINAL_TEST_REQUIRED_FOR_PROMOTION,
    OOS_SUMMARY_REQUIRED,
    classify_research_mode,
)
from .validation.promotion import AUTO_FULL_NOT_PROMOTABLE, evaluate_promotion_provenance, research_only_provenance
from .validation.split import OOSConfig
from .wq_brain_service import (
    run_batch_simulation,
    run_check_alphas,
    run_list_alphas,
    run_single_simulation,
    run_submit_by_ids,
)
from .wq_submission_guard import require_submission_preflight, wq_target_scope

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s", stream=sys.stderr)
logger = logging.getLogger(__name__)
_MCP_BACKGROUND_TASKS: set[asyncio.Task] = set()

mcp = FastMCP(
    "quantgpt",
    instructions=(
        "QuantGPT — A 股因子回测服务。先用 list_operators 了解可用算子。"
        "单股研究先用 get_stock_history/check_market_cache 读本地缓存，"
        "不要直接把单股问题升级为全 CSI500 因子回测。"
        "用于研究结论或候选选择时，score_factor/run_backtest 默认走 OOS selection："
        "train 定方向，valid 选候选，test 仅在 validation_stage=final 时最终验收。"
    ),
    streamable_http_path="/",
    stateless_http=True,
    transport_security=TransportSecuritySettings(
        allowed_hosts=["localhost", "localhost:8003", "127.0.0.1", "127.0.0.1:8003"],
    ),
)


def _enrich_with_fundamentals(
    expression: str,
    market_df,
    stock_codes: list,
    start_date: str,
    end_date: str,
    allow_remote_fetch: bool = True,
    cancel_check: Callable[[], None] | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
):
    """Conditionally fetch and merge fundamental data if the expression uses fundamental vars."""
    from .fundamental_data import detect_fundamental_vars, enrich_market_data
    fund_vars = detect_fundamental_vars(expression)
    return enrich_market_data(
        market_df,
        fund_vars,
        stock_codes,
        start_date,
        end_date,
        allow_remote_fetch=allow_remote_fetch,
        cancel_check=cancel_check,
        progress_callback=progress_callback,
    )


def _submit_mcp_background_task(coro) -> None:
    task = asyncio.create_task(coro)
    _MCP_BACKGROUND_TASKS.add(task)

    def _discard(done: asyncio.Task) -> None:
        _MCP_BACKGROUND_TASKS.discard(done)
        try:
            done.result()
        except Exception:
            logger.error("MCP background task crashed: %s", traceback.format_exc())

    task.add_done_callback(_discard)


async def _run_mcp_tool_with_existing_task(task_id: str, coro_factory: Callable[[], Awaitable[str]]) -> None:
    token = force_mcp_task_id(task_id)
    try:
        await coro_factory()
    finally:
        reset_forced_mcp_task_id(token)


async def _await_mcp_future_result(future, task_id: str, timeout_seconds: float = 600):
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            _mcp_cancel_check(task_id)
        except CancelledException:
            future.cancel()
            raise
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return await asyncio.to_thread(future.result, 0)
        try:
            return await asyncio.to_thread(future.result, min(2.0, remaining))
        except FutureTimeoutError:
            continue


def _submitted_mcp_task_response(task_id: str) -> str:
    return json.dumps({
        "submitted": True,
        "task_id": task_id,
        "status": "running",
        "poll_tool": "get_mcp_task_status",
        "cancel_tool": "cancel_mcp_task",
    }, ensure_ascii=False, indent=2)


def _mcp_cancel_check(task_id: str) -> None:
    check_cancelled(task_id)


def _mcp_fetch_progress_callback(
    task_id: str,
    *,
    status: str,
    stage: str,
    base_progress: int,
    span: int,
) -> Callable[[int, int, str], None]:
    def _callback(done: int, total: int, message: str) -> None:
        pct = base_progress
        if total > 0:
            pct = base_progress + int((max(0, min(done, total)) / total) * span)
        update_mcp_task_progress_sync(
            task_id,
            status=status,
            stage=stage,
            progress=pct,
            progress_current=done,
            progress_total=total,
            progress_message=message,
        )

    return _callback


def _mcp_cancelled_result(task_id: str) -> dict:
    return {
        "error_code": "MCP_TASK_CANCELLED",
        "task_id": task_id,
        "status": "cancelled",
        "hint": "The task was cancelled cooperatively. A blocking upstream data call may finish before cancellation is observed.",
    }


def _market_data_unavailable_result(allow_remote_fetch: bool) -> dict:
    result = {"error_code": "MARKET_DATA_UNAVAILABLE", "error": "No market data available."}
    if allow_remote_fetch:
        result["hint"] = "Check the universe/date range and upstream data provider availability."
    else:
        result["hint"] = (
            "MCP factor tools default to local-cache-only data access to avoid long silent agent calls. "
            "Prewarm data first, choose a cached date range, or pass allow_remote_fetch=true for a blocking remote fetch."
        )
    return result


class _RemotePrefetchRequired(RuntimeError):
    def __init__(self, payload: dict):
        super().__init__(payload.get("error") or payload.get("error_code") or "REMOTE_PREFETCH_REQUIRED")
        self.payload = payload


def _remote_fetch_stock_limit() -> int:
    raw = os.environ.get("QUANTGPT_MCP_REMOTE_FETCH_STOCK_LIMIT", "50")
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid QUANTGPT_MCP_REMOTE_FETCH_STOCK_LIMIT=%r, using 50", raw)
        return 50
    return max(0, value)


def _suggest_prewarm_command(universe: str, start_date: str, end_date: str) -> str:
    return (
        f".venv/bin/python scripts/prewarm.py --universe {universe} --start {start_date} --end {end_date} "
        "--skip-fundamentals --skip-dividends --skip-factors"
    )


def _remote_prefetch_required_result(
    *,
    universe: str,
    universe_date: str,
    start_date: str,
    end_date: str,
    plan: dict,
    threshold: int,
) -> dict:
    return {
        "error_code": "REMOTE_PREFETCH_REQUIRED",
        "error": (
            "Remote market-data fetch would require too many stock cache fills for a normal MCP call."
        ),
        "hint": (
            "Run an explicit cache prewarm first, narrow the date range/universe, or raise "
            "QUANTGPT_MCP_REMOTE_FETCH_STOCK_LIMIT for this environment."
        ),
        "universe": universe,
        "universe_date": universe_date,
        "start_date": start_date,
        "end_date": end_date,
        "remote_fetch_stock_count": plan["fetch_required_count"],
        "missing_stock_count": plan["missing_stock_count"],
        "partial_stock_count": plan["partial_stock_count"],
        "threshold": threshold,
        "available_cache_months": list_universe_cache_months(universe),
        "fetch_required_stock_codes_sample": plan["fetch_required_stock_codes_sample"],
        "missing_stock_codes_sample": plan["missing_stock_codes_sample"],
        "partial_stock_codes_sample": plan["partial_stock_codes_sample"],
        "suggested_prewarm_command": _suggest_prewarm_command(universe, start_date, end_date),
    }


def _raise_if_remote_prefetch_required(
    *,
    universe: str,
    universe_date: str,
    start_date: str,
    end_date: str,
    stock_codes: list[str],
) -> None:
    threshold = _remote_fetch_stock_limit()
    plan = plan_stock_cache_fetch(stock_codes, start_date, end_date)
    if plan["fetch_required_count"] > threshold:
        raise _RemotePrefetchRequired(_remote_prefetch_required_result(
            universe=universe,
            universe_date=universe_date,
            start_date=start_date,
            end_date=end_date,
            plan=plan,
            threshold=threshold,
        ))


async def _record_market_data_prefetch_required(
    *,
    tool_name: str,
    task_id: str,
    expression: str,
    params: dict,
    exc: _RemotePrefetchRequired,
) -> str:
    result = dict(exc.payload)
    result.update(await _record_mcp_experiment_failure(
        tool_name=tool_name,
        task_id=task_id,
        expression=expression,
        params=params,
        status="data_prefetch_required",
        failure_reason=result["error_code"],
    ))
    return json.dumps(result, ensure_ascii=False, indent=2, default=str)


def _resolve_universe_date(universe_date: str | None, fallback_date: str) -> str:
    return universe_date or fallback_date


def _fetch_data_for_market(
    universe: str,
    start_date: str,
    end_date: str,
    allow_remote_fetch: bool = True,
    universe_date: str | None = None,
    cancel_check: Callable[[], None] | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
):
    """Fetch market data and stock codes. Returns (market_df, stock_codes)."""
    cache_only = not allow_remote_fetch
    resolved_universe_date = _resolve_universe_date(universe_date, start_date)
    stock_codes = get_universe(universe, date=resolved_universe_date, cache_only=cache_only)
    if allow_remote_fetch and stock_codes:
        _raise_if_remote_prefetch_required(
            universe=universe,
            universe_date=resolved_universe_date,
            start_date=start_date,
            end_date=end_date,
            stock_codes=stock_codes,
        )
    fetcher = MarketDataFetcher()
    if cancel_check:
        cancel_check()
    market_df = fetcher.fetch_stocks(
        stock_codes,
        start_date,
        end_date,
        cache_only=cache_only,
        cancel_check=cancel_check,
        progress_callback=progress_callback,
    )
    if cancel_check:
        cancel_check()
    return market_df, stock_codes


def _fetch_benchmark_for_market(benchmark: str, start_date: str, end_date: str, allow_remote_fetch: bool = True):
    """Fetch benchmark returns."""
    return fetch_benchmark_returns(benchmark, start_date, end_date, cache_only=not allow_remote_fetch)


def _market_data_provenance_fields(
    market_df: pd.DataFrame,
    *,
    universe: str,
    start_date: str,
    end_date: str,
    universe_date: str | None = None,
    stock_codes: list[str],
    endpoint: str,
) -> dict:
    source_metadata = market_df.attrs.get("source_metadata")
    if not isinstance(source_metadata, dict):
        source_metadata = {}
    snapshot = ensure_market_frame_snapshot(
        market_df,
        vendor=str(market_df.attrs.get("data_source") or source_metadata.get("vendor") or "in_memory_frame"),
        source_kind=str(source_metadata.get("source_kind") or "market_dataframe_snapshot"),
        endpoint=endpoint,
        query_params={
            "universe": universe,
            "start_date": start_date,
            "end_date": end_date,
            "universe_date": universe_date,
            "stock_codes": stock_codes,
        },
        source_metadata=source_metadata,
    )
    return snapshot_result_fields(snapshot, source_metadata=source_metadata)


def _annotate_data_provenance(report: dict | None, provenance: dict) -> None:
    if report is None:
        return
    report.setdefault("data_snapshot_id", provenance["data_snapshot_id"])
    report.setdefault("data_source", provenance.get("data_source"))


def _score_oos_for_stage(oos_result: dict, data_quality_report: dict | None, validation_stage: str) -> dict:
    if validation_stage == "selection":
        return compute_oos_selection_score(oos_result, data_quality=data_quality_report)
    return compute_oos_score(oos_result, data_quality=data_quality_report)


def _oos_blockers_for_stage(validation_stage: str) -> list[str]:
    if validation_stage == "selection":
        return [FINAL_TEST_REQUIRED_FOR_PROMOTION, FINAL_TEST_NOT_RUN, "FULL_VALIDATION_SUITE_NOT_RUN"]
    return ["FULL_VALIDATION_SUITE_NOT_RUN"]


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _research_only_blockers(params: dict) -> list[str]:
    policy = classify_research_mode(params)
    blockers = list(policy.get("policy_blockers") or [])
    if not params.get("oos_enabled"):
        if policy.get("direction_policy") == "auto_full":
            blockers.extend([BIASED_DIRECTION_MODE, AUTO_FULL_NOT_PROMOTABLE])
        blockers.extend([OOS_SUMMARY_REQUIRED, "OOS_TRAIN_VALID_TEST_NOT_RUN"])
    return _dedupe(blockers)


def _attach_policy_metadata(payload: dict) -> dict:
    policy = classify_research_mode(payload.get("params"))
    payload["research_mode"] = policy["research_mode"]
    payload["direction_policy"] = policy["direction_policy"]
    payload["formal_safe"] = policy["formal_safe"]
    payload["final_test_policy"] = policy["final_test_policy"]
    return policy


def _get_ledger_session_factory():
    from .db import _get_session_factory

    return _get_session_factory()


def _run_ledger_sync(coro_factory):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro_factory())
    logger.warning("Synchronous MCP ledger write skipped inside running event loop")
    return None


def _experiment_exists_sync(experiment_id: str) -> bool | None:
    async def _exists() -> bool:
        factory = _get_ledger_session_factory()
        async with factory() as session:
            return await _ledger_get_experiment(session, experiment_id) is not None

    return _run_ledger_sync(_exists)


def _record_strategy_export_event_sync(result_payload: dict, export_payload: dict) -> None:
    experiment_id = result_payload.get("experiment_id") or export_payload.get("experiment_id")
    if not isinstance(experiment_id, str) or not experiment_id:
        return

    async def _record() -> None:
        factory = _get_ledger_session_factory()
        async with factory() as session:
            row = await _ledger_get_experiment(session, experiment_id)
            if row is None:
                return
            await _ledger_record_export_event(
                session,
                experiment_id=experiment_id,
                schema_version=str(export_payload.get("schema_version") or "strategy_signal.v1"),
                export_path=export_payload.get("json_path"),
                payload=export_payload,
            )
            if row.status == "candidate":
                await _ledger_transition_status(session, experiment_id, "exported")
            await session.commit()

    _run_ledger_sync(_record)


async def _record_mcp_experiment_result(
    *,
    tool_name: str,
    task_id: str | None,
    expression: str,
    payload: dict,
    status: str | None = None,
    failure_reason: str | None = None,
) -> None:
    """Best-effort MCP ledger write; never fail the tool response."""
    try:
        params = dict(payload.get("params") or {})
        params.setdefault("source", "mcp")
        params.setdefault("direction_policy", payload.get("direction_policy"))
        params.setdefault("research_mode", payload.get("research_mode"))
        status = status or ("validated_oos" if params.get("oos_enabled") else "backtested_train")
        factory = _get_ledger_session_factory()
        async with factory() as session:
            snapshot = payload.get("data_source_metadata")
            if isinstance(snapshot, dict) and snapshot.get("snapshot_id"):
                await persist_data_snapshot(session, snapshot)
            experiment = await _ledger_record_experiment(
                session,
                expression=expression,
                params=params,
                status=status,
                task_id=task_id,
                parent_experiment_id=params.get("parent_experiment_id"),
                created_by=f"mcp:{tool_name}",
                result_summary=_ledger_result_summary(payload),
                failure_reason=failure_reason,
            )
            await _ledger_record_experiment_result(
                session,
                experiment_id=experiment.experiment_id,
                stage=status,
                validation_stage=params.get("validation_stage"),
                train_period=_period_for(payload, "train"),
                validation_period=_period_for(payload, "valid"),
                test_period=_period_for(payload, "test"),
                direction_policy=payload.get("direction_policy"),
                metrics=_ledger_metrics(payload),
                oos_score=payload.get("oos_score") or payload.get("selection_score") or payload.get("final_oos_score"),
                data_quality=payload.get("data_quality"),
                failure_reason=failure_reason,
            )
            report_path = payload.get("report_path")
            if isinstance(report_path, str) and report_path:
                await _ledger_record_experiment_artifact(
                    session,
                    experiment_id=experiment.experiment_id,
                    artifact_type="report",
                    uri=report_path,
                    metadata={"tool": tool_name},
                )
            artifact_type = payload.get("artifact_type")
            artifact_uri = payload.get("artifact_uri")
            if isinstance(artifact_type, str) and isinstance(artifact_uri, str):
                await _ledger_record_experiment_artifact(
                    session,
                    experiment_id=experiment.experiment_id,
                    artifact_type=artifact_type,
                    uri=artifact_uri,
                    metadata=payload.get("artifact_metadata") if isinstance(payload.get("artifact_metadata"), dict) else None,
                )
            payload["experiment_id"] = experiment.experiment_id
            payload["factor_hash"] = experiment.factor_hash
            payload["config_hash"] = experiment.config_hash
            await session.commit()
    except Exception as exc:
        logger.warning("MCP experiment ledger write failed: %s", exc)


async def _record_mcp_experiment_failure(
    *,
    tool_name: str,
    task_id: str,
    expression: str,
    params: dict,
    status: str,
    failure_reason: str,
) -> dict:
    payload = {
        "params": dict(params),
        "research_mode": classify_research_mode(params).get("research_mode"),
        "direction_policy": classify_research_mode(params).get("direction_policy"),
        "formal_safe": False,
        "failure_reason": failure_reason,
    }
    await _record_mcp_experiment_result(
        tool_name=tool_name,
        task_id=task_id,
        expression=expression,
        payload=payload,
        status=status,
        failure_reason=failure_reason,
    )
    return {
        "experiment_id": payload.get("experiment_id"),
        "factor_hash": payload.get("factor_hash"),
    }


def _ledger_result_summary(payload: dict) -> dict:
    return {
        "score": payload.get("score") or (payload.get("scoring") or {}).get("score"),
        "grade": payload.get("grade") or (payload.get("scoring") or {}).get("grade"),
        "decision": payload.get("decision") or (payload.get("scoring") or {}).get("decision"),
        "promotion_state": payload.get("promotion_state"),
        "promotion_blockers": payload.get("promotion_blockers"),
        "report_scope": payload.get("report_scope"),
    }


def _ledger_metrics(payload: dict) -> dict:
    if isinstance(payload.get("key_metrics"), dict):
        return payload["key_metrics"]
    metrics = {}
    if isinstance(payload.get("metrics"), dict):
        metrics["report_metrics"] = payload["metrics"]
    if isinstance(payload.get("backtest_summary"), dict):
        metrics["backtest_summary"] = payload["backtest_summary"]
    return metrics


def _period_for(payload: dict, stage: str) -> list | None:
    oos_result = payload.get("oos_result")
    if not isinstance(oos_result, dict):
        return None
    stage_payload = oos_result.get(stage)
    if not isinstance(stage_payload, dict):
        return None
    period = stage_payload.get("period")
    return period if isinstance(period, list) else None


def _serialize_experiment(row) -> dict:
    return {
        "experiment_id": row.experiment_id,
        "factor_hash": row.factor_hash,
        "expression": row.expression,
        "expression_normalized": row.expression_normalized,
        "status": row.status,
        "universe": row.universe,
        "market": row.market,
        "asset_class": row.asset_class,
        "data_snapshot_id": row.data_snapshot_id,
        "direction_policy": row.direction_policy,
        "research_mode": row.research_mode,
        "task_id": row.task_id,
        "parent_experiment_id": row.parent_experiment_id,
        "result_summary": row.result_summary,
        "failure_reason": row.failure_reason,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _serialize_experiment_detail(row) -> dict:
    payload = _serialize_experiment(row)
    payload["results"] = [
        {
            "stage": result.stage,
            "validation_stage": result.validation_stage,
            "direction_policy": result.direction_policy,
            "metrics": result.metrics,
            "oos_score": result.oos_score,
            "data_quality": result.data_quality,
            "failure_reason": result.failure_reason,
            "created_at": result.created_at.isoformat() if result.created_at else None,
        }
        for result in row.results
    ]
    payload["artifacts"] = [
        {
            "artifact_type": artifact.artifact_type,
            "uri": artifact.uri,
            "content_hash": artifact.content_hash,
            "metadata": artifact.artifact_metadata,
            "created_at": artifact.created_at.isoformat() if artifact.created_at else None,
        }
        for artifact in row.artifacts
    ]
    payload["promotion_events"] = [
        {
            "boundary": event.boundary,
            "decision": event.decision,
            "blockers": event.blockers,
            "provenance": event.provenance,
            "created_at": event.created_at.isoformat() if event.created_at else None,
        }
        for event in row.promotion_events
    ]
    payload["export_events"] = [
        {
            "schema_version": event.schema_version,
            "export_path": event.export_path,
            "payload_hash": event.payload_hash,
            "created_at": event.created_at.isoformat() if event.created_at else None,
        }
        for event in row.export_events
    ]
    return payload


# Dummy DataFrame for expression validation (includes fundamental columns)
_VALIDATION_DUMMY = pd.DataFrame({
    "open": [1.0, 2.0, 3.0], "high": [1.1, 2.1, 3.1],
    "low": [0.9, 1.9, 2.9], "close": [1.0, 2.0, 3.0],
    "volume": [100, 200, 300], "amount": [100, 400, 900],
    "pct_change": [0, 100, 50],
    "trade_date": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]),
    **{name: [1.0, 1.1, 1.2] for name in ALL_FUNDAMENTAL_NAMES},
})


@mcp.tool()
def list_operators() -> str:
    """返回因子表达式支持的全部操作符及用法说明。Agent 据此生成因子表达式。"""
    return _expr_module_doc or _OPERATORS_DOC


@mcp.tool()
def list_universes() -> str:
    """返回可用股票池列表及说明。"""
    a_share_info = {
        "small_scale": f"5 只蓝筹股（快速测试）: {UNIVERSES['small_scale']}",
        "hs300": "沪深300成分股（动态获取）",
        "csi500": "中证500成分股（动态获取）",
        "csi1000": "中证1000成分股（派生: 全A - HS300 - CSI500, 取前1000）",
        "csi2000": "中证2000成分股（派生: 全A - HS300 - CSI500 - CSI1000, 取前2000）",
    }
    a_share_benchmarks = {k: v["name"] for k, v in BENCHMARK_CODES.items()}
    return json.dumps({
        "universes": a_share_info,
        "benchmarks": a_share_benchmarks,
    }, ensure_ascii=False, indent=2)


def _json_market_value(value):
    if value is None or pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    if hasattr(value, "item"):
        value = value.item()
    return value


def _stock_history_payload(stock_code: str, start_date: str = "", end_date: str = "", limit: int = 120) -> dict:
    fetcher = MarketDataFetcher()
    normalized_code = fetcher._normalize_stock_code(stock_code)
    cache_path = fetcher._cache_path(normalized_code)
    cache_info = describe_stock_cache(normalized_code, start_date, end_date, cache_dir=fetcher.cache_dir)
    if cache_info["cache_status"] != "hit":
        return {
            "error_code": "STOCK_CACHE_MISSING",
            "cache_status": cache_info["cache_status"],
            "stock_code": normalized_code,
            "cache_path": cache_path,
            "request_range": {
                "start_date": start_date or None,
                "end_date": end_date or None,
            },
            "hint": (
                "Single-stock research reads only local data/stocks parquet cache. "
                "Prewarm this stock before using factor or backtest tools."
            ),
        }

    df = fetcher._load_cache(normalized_code)
    if df is None or df.empty:
        return {
            "error_code": "STOCK_CACHE_MISSING",
            "cache_status": "empty",
            "stock_code": normalized_code,
            "cache_path": cache_path,
        }

    df = df.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    filtered = df
    if start_date:
        filtered = filtered[filtered["trade_date"] >= pd.Timestamp(start_date)]
    if end_date:
        filtered = filtered[filtered["trade_date"] <= pd.Timestamp(end_date)]
    filtered = filtered.sort_values("trade_date")
    limit = max(1, min(int(limit), 500))
    row_columns = [
        column
        for column in ("trade_date", "stock_code", "open", "high", "low", "close", "volume", "amount", "pct_change")
        if column in filtered.columns
    ]
    rows = [
        {column: _json_market_value(row[column]) for column in row_columns}
        for _, row in filtered.tail(limit).iterrows()
    ]
    latest = df.sort_values("trade_date").iloc[-1]
    return {
        "stock_code": normalized_code,
        "cache_status": "hit",
        "cache_path": cache_path,
        "cache_range": {
            "start_date": cache_info["cache_start_date"],
            "end_date": cache_info["cache_end_date"],
            "row_count": cache_info["row_count"],
        },
        "request_range": {
            "start_date": start_date or None,
            "end_date": end_date or None,
        },
        "range_covered": cache_info["range_covered"],
        "returned_rows": len(rows),
        "rows": rows,
        "summary": {
            "latest_trade_date": _json_market_value(latest.get("trade_date")),
            "latest_close": _json_market_value(latest.get("close")),
            "latest_pct_change": _json_market_value(latest.get("pct_change")),
            "latest_amount": _json_market_value(latest.get("amount")),
        },
    }


@mcp.tool()
def get_stock_history(stock_code: str, start_date: str = "", end_date: str = "", limit: int = 120) -> str:
    """读取单只 A 股本地行情缓存，不触发远程拉取。"""
    try:
        return json.dumps(
            _stock_history_payload(stock_code, start_date=start_date, end_date=end_date, limit=limit),
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    except Exception as exc:
        return json.dumps({"error_code": "STOCK_HISTORY_FAILED", "hint": str(exc)}, ensure_ascii=False)


@mcp.tool()
def check_market_cache(
    universe: str = "csi500",
    start_date: str = "",
    end_date: str = "",
    stock_code: str = "",
) -> str:
    """检查股票池月度缓存和可选单股 parquet 覆盖情况，不触发远程拉取。"""
    universe_date = start_date or end_date or date.today().isoformat()
    cache_path = universe_cache_path(universe, universe_date)
    if universe in UNIVERSES:
        codes = list(UNIVERSES[universe])
        cache_status = "static"
        cache_exists = True
    else:
        codes = read_cached_universe(universe, universe_date)
        cache_status = "hit" if codes else "missing"
        cache_exists = cache_path.exists()

    payload = {
        "universe": universe,
        "universe_date": universe_date,
        "universe_cache_month": universe_date[:7],
        "universe_cache_path": str(cache_path),
        "universe_cache_exists": cache_exists,
        "universe_cache_status": cache_status,
        "universe_stock_count": len(codes),
        "available_cache_months": list_universe_cache_months(universe),
    }
    if stock_code:
        fetcher = MarketDataFetcher()
        normalized_code = fetcher._normalize_stock_code(stock_code)
        payload["stock_code"] = normalized_code
        payload["stock_in_universe"] = normalized_code in codes if codes else False
        payload["stock_cache"] = describe_stock_cache(
            normalized_code,
            start_date=start_date,
            end_date=end_date,
            cache_dir=fetcher.cache_dir,
        )
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


@mcp.tool()
def list_markets() -> str:
    """返回策略框架支持的市场和能力描述。"""
    return _strategy_dumps(_list_strategy_markets())


@mcp.tool()
def list_data_fields(market: str = "a_share") -> str:
    """返回指定市场可用于 StrategySpec 因子表达式的数据字段。"""
    try:
        return _strategy_dumps(_list_strategy_data_fields(market))
    except Exception as e:
        return _strategy_dumps({"error_code": "MARKET_UNSUPPORTED", "hint": str(e)})


@mcp.tool()
def list_strategy_templates() -> str:
    """返回 Post-MVP 策略模板和治理边界。"""
    return _strategy_dumps(_list_strategy_templates_payload())


@mcp.tool()
def get_strategy_template(template_id: str) -> str:
    """返回指定策略模板的 StrategySpec 和治理元数据。"""
    try:
        return _strategy_dumps(_get_strategy_template_payload(template_id))
    except Exception as e:
        return _strategy_dumps({"error_code": "STRATEGY_TEMPLATE_NOT_FOUND", "hint": str(e)})


@mcp.tool()
def instantiate_strategy_template(template_id: str, overrides: dict | None = None) -> str:
    """按模板生成可校验 StrategySpec，可用点路径 overrides 修改参数。"""
    try:
        return _strategy_dumps(_instantiate_strategy_template_payload(template_id, overrides=overrides))
    except Exception as e:
        return _strategy_dumps({"error_code": "STRATEGY_TEMPLATE_INSTANTIATE_FAILED", "hint": str(e)})


@mcp.tool()
def validate_strategy_spec(spec: dict) -> str:
    """校验 StrategySpec v0/v1，失败时返回 error_code 和 hint。"""
    return _strategy_dumps(_validate_strategy_payload(spec))


@mcp.tool()
async def run_strategy_backtest(
    spec: dict,
    start_date: str,
    end_date: str,
    benchmark: str = "hs300",
    universe_date: str | None = None,
    rebalance_anchor: str | None = None,
) -> str:
    """运行 StrategySpec v0/v1 策略回测；v1 OOS 返回 train/valid/test、data_quality 和 oos_score。"""
    request_data = {
        "spec": spec,
        "start_date": start_date,
        "end_date": end_date,
        "benchmark": benchmark,
        "universe_date": universe_date,
        "rebalance_anchor": rebalance_anchor,
    }
    try:
        payload = await asyncio.to_thread(_run_strategy_backtest_payload, request_data)
        return _strategy_dumps(payload)
    except Exception as e:
        return _strategy_dumps({"error_code": "STRATEGY_BACKTEST_FAILED", "hint": str(e)})


@mcp.tool()
def score_strategy(result: dict) -> str:
    """根据 run_strategy_backtest 输出计算策略级评分。"""
    try:
        return _strategy_dumps(_score_strategy_payload(result))
    except Exception as e:
        return _strategy_dumps({"error_code": "STRATEGY_SCORE_FAILED", "hint": str(e)})


@mcp.tool()
async def generate_strategy_report(result: dict) -> str:
    """根据 run_strategy_backtest 输出生成策略 HTML 报告和 summary JSON。"""
    try:
        payload = await asyncio.to_thread(_generate_strategy_report_payload, result)
        return _strategy_dumps(payload)
    except Exception as e:
        return _strategy_dumps({"error_code": "STRATEGY_REPORT_FAILED", "hint": str(e)})


@mcp.tool()
def export_strategy_candidate(result: dict) -> str:
    """导出候选调仓信号 JSON/CSV 友好的结构，不包含下单或券商字段。"""
    try:
        payload = _export_strategy_candidate_payload(result)
        _record_strategy_export_event_sync(result, payload)
        return _strategy_dumps(payload)
    except Exception as e:
        return _strategy_dumps({"error_code": "STRATEGY_EXPORT_FAILED", "hint": str(e)})


@mcp.tool()
def diagnose_strategy(result: dict) -> str:
    """输出策略诊断 taxonomy 和可执行的 spec 调整建议。"""
    try:
        return _strategy_dumps(_diagnose_strategy_payload(result))
    except Exception as e:
        return _strategy_dumps({"error_code": "STRATEGY_DIAGNOSIS_FAILED", "hint": str(e)})


@mcp.tool()
def run_strategy_anti_overfit(result: dict) -> str:
    """基于策略回测结果执行策略级反过拟合摘要检查。"""
    try:
        return _strategy_dumps(_run_strategy_anti_overfit_payload(result))
    except Exception as e:
        return _strategy_dumps({"error_code": "STRATEGY_ANTI_OVERFIT_FAILED", "hint": str(e)})


@mcp.tool()
def run_strategy_rolling_validation(result: dict, windows: int = 3) -> str:
    """基于策略回测收益执行策略级 rolling validation 摘要。"""
    try:
        return _strategy_dumps(_run_strategy_rolling_validation_payload(result, windows=windows))
    except Exception as e:
        return _strategy_dumps({"error_code": "STRATEGY_ROLLING_VALIDATION_FAILED", "hint": str(e)})


@mcp.tool()
def optimize_strategy_candidate(signals: list[dict], spec: dict) -> str:
    """按 StrategySpec 风控约束优化候选信号权重，不生成真实订单。"""
    try:
        return _strategy_dumps(_optimize_candidate_weights_payload(signals, spec))
    except Exception as e:
        return _strategy_dumps({"error_code": "STRATEGY_OPTIMIZE_FAILED", "hint": str(e)})


@mcp.tool()
async def validate_expression(expression: str, mode: str = "local") -> str:
    """验证因子表达式语法是否正确。返回 OK 或错误信息。

    Args:
        expression: 因子表达式
        mode: "local"（本地回测验证，默认）或 "wq"（WQ BRAIN 提交验证，放宽字段/算子限制）
    """

    task_id = await start_mcp_task("validate_expression", expression, {"mode": mode})
    result_text = ""
    error_text = None
    params = {"mode": mode, "source": "mcp"}
    try:
        depth = 0
        for i, ch in enumerate(expression):
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
                if depth < 0:
                    error_text = f"括号不平衡：位置 {i} 处多余的右括号 ')'"
                    await _record_mcp_experiment_failure(
                        tool_name="validate_expression",
                        task_id=task_id,
                        expression=expression,
                        params=params,
                        status="parse_failed",
                        failure_reason=error_text,
                    )
                    return f"ERROR: {error_text}"
        if depth > 0:
            error_text = f"括号不平衡：缺少 {depth} 个右括号 ')'"
            await _record_mcp_experiment_failure(
                tool_name="validate_expression",
                task_id=task_id,
                expression=expression,
                params=params,
                status="parse_failed",
                failure_reason=error_text,
            )
            return f"ERROR: {error_text}"

        try:
            func = parse_expression(expression, mode=mode)
            if mode == "wq":
                result_text = "OK: expression is valid for WQ BRAIN submission"
            else:
                func(_VALIDATION_DUMMY)
                result_text = "OK: expression is valid"
            await _record_mcp_experiment_result(
                tool_name="validate_expression",
                task_id=task_id,
                expression=expression,
                payload={"params": params, "result_summary": {"validation": "parsed"}},
                status="parsed",
            )
            return result_text
        except Exception as exc:
            error_text = str(exc)
            await _record_mcp_experiment_failure(
                tool_name="validate_expression",
                task_id=task_id,
                expression=expression,
                params=params,
                status="parse_failed",
                failure_reason=error_text,
            )
            return f"ERROR: {exc}"
    finally:
        result_payload = {"message": result_text} if result_text else None
        await complete_mcp_task(task_id, result_payload, error_text, expression)


@mcp.tool()
async def get_mcp_task_status(task_id: str, include_result: bool = False) -> str:
    """查询 MCP 后台任务状态、进度和可选最终结果。"""
    return json.dumps(
        get_mcp_task_status_payload(task_id, include_result=include_result),
        ensure_ascii=False,
        indent=2,
        default=str,
    )


@mcp.tool()
async def cancel_mcp_task(task_id: str) -> str:
    """请求协作式取消 MCP 后台任务。"""
    payload = await request_mcp_task_cancel(task_id)
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


@mcp.tool()
async def run_backtest(
    expression: str,
    universe: str = "hs300",
    start_date: str = "2023-01-01",
    end_date: str = "2025-12-31",
    universe_date: str | None = None,
    n_groups: int = 5,
    holding_period: int = 5,
    benchmark: str = "hs300",
    neutralize_industry: bool = True,
    neutralize_cap: bool = True,
    rebalance_anchor: str | None = None,
    oos_enabled: bool = True,
    validation_stage: Literal["selection", "final"] = "selection",
    direction_mode: str = "auto_full",
    fixed_direction: int | None = None,
    data_quality: bool | None = None,
    data_quality_mode: Literal["report_only", "filter", "strict"] = "filter",
    max_abs_daily_ret: float = 0.25,
    max_missing_ratio_per_stock: float = 0.2,
    adjustment: Literal["qfq", "hfq", "none", "unknown"] = "unknown",
    allow_remote_fetch: bool = False,
    submit_only: bool = False,
) -> str:
    """执行因子回测,生成 QuantStats HTML 报告。

    Args:
        expression: 因子表达式,如 "rank(close/ts_mean(close, 20))"
        universe: 股票池名称 (small_scale/hs300/csi500/csi1000/csi2000)
        start_date: 回测起始日期 YYYY-MM-DD
        end_date: 回测结束日期 YYYY-MM-DD
        universe_date: 股票池成分股基准日期；默认使用 start_date
        n_groups: 分组数量
        holding_period: 持仓周期(交易日)
        benchmark: 基准 (hs300/zz500/sz50/csi1000)
        neutralize_industry: 行业中性化(默认开启)
        neutralize_cap: 市值中性化(默认开启)
        rebalance_anchor: 换仓网格锚定日期
        oos_enabled: 启用训练/验证/测试样本外评估；默认开启 OOS selection
        validation_stage: selection 只用 train+valid 选候选；final 才运行并暴露 test
        direction_mode: 非 OOS 回测方向模式，auto_full 或 fixed
        fixed_direction: fixed 模式方向，1=高值做多，-1=低值做多
        data_quality: 是否运行基础行情数据质量门；None 表示兼容默认
        allow_remote_fetch: 是否允许 MCP 工具在缓存缺失时阻塞式拉取远程行情；默认 False
        submit_only: 是否异步提交并立即返回 task_id；默认 False 保持同步行为

    Returns:
        JSON string with report_path, metrics, group_returns, anti_overfit.
    """
    resolved_universe_date = _resolve_universe_date(universe_date, start_date)
    task_params = {
        "universe": universe, "start_date": start_date, "end_date": end_date,
        "universe_date": resolved_universe_date,
        "n_groups": n_groups, "holding_period": holding_period, "benchmark": benchmark,
        "neutralize_industry": neutralize_industry, "neutralize_cap": neutralize_cap,
        "rebalance_anchor": rebalance_anchor, "oos_enabled": oos_enabled, "validation_stage": validation_stage,
        "direction_mode": direction_mode, "fixed_direction": fixed_direction,
        "data_quality": data_quality,
        "allow_remote_fetch": allow_remote_fetch,
    }
    task_id = await start_mcp_task("backtest", expression, task_params)
    if submit_only:
        await update_mcp_task_progress(
            task_id,
            status="running",
            progress=0,
            progress_message="submitted run_backtest",
            stage="submitted",
        )
        _submit_mcp_background_task(_run_mcp_tool_with_existing_task(
            task_id,
            lambda: run_backtest(
                expression,
                universe=universe,
                start_date=start_date,
                end_date=end_date,
                universe_date=universe_date,
                n_groups=n_groups,
                holding_period=holding_period,
                benchmark=benchmark,
                neutralize_industry=neutralize_industry,
                neutralize_cap=neutralize_cap,
                rebalance_anchor=rebalance_anchor,
                oos_enabled=oos_enabled,
                validation_stage=validation_stage,
                direction_mode=direction_mode,
                fixed_direction=fixed_direction,
                data_quality=data_quality,
                data_quality_mode=data_quality_mode,
                max_abs_daily_ret=max_abs_daily_ret,
                max_missing_ratio_per_stock=max_missing_ratio_per_stock,
                adjustment=adjustment,
                allow_remote_fetch=allow_remote_fetch,
                submit_only=False,
            ),
        ))
        return _submitted_mcp_task_response(task_id)
    _error_msg = None
    _result = None
    try:
        await update_mcp_task_progress(
            task_id,
            status="validating",
            progress=2,
            progress_message="validating run_backtest request",
            stage="validating",
        )
        _mcp_cancel_check(task_id)
        if validation_stage not in {"selection", "final"}:
            _result = {
                "error_code": "INVALID_VALIDATION_STAGE",
                "hint": "validation_stage must be selection or final",
            }
            _result.update(await _record_mcp_experiment_failure(
                tool_name="run_backtest",
                task_id=task_id,
                expression=expression,
                params=task_params,
                status="rejected",
                failure_reason=_result["error_code"],
            ))
            return json.dumps(_result)
        if validation_stage == "final" and not oos_enabled:
            _result = {
                "error_code": "INVALID_VALIDATION_STAGE",
                "hint": "validation_stage=final requires oos_enabled=true",
            }
            _result.update(await _record_mcp_experiment_failure(
                tool_name="run_backtest",
                task_id=task_id,
                expression=expression,
                params=task_params,
                status="rejected",
                failure_reason=_result["error_code"],
            ))
            return json.dumps(_result)
        if direction_mode not in {"auto_full", "fixed"}:
            _result = {"error_code": "INVALID_DIRECTION_POLICY", "hint": "direction_mode must be auto_full or fixed"}
            _result.update(await _record_mcp_experiment_failure(
                tool_name="run_backtest",
                task_id=task_id,
                expression=expression,
                params=task_params,
                status="rejected",
                failure_reason=_result["error_code"],
            ))
            return json.dumps(_result)
        if oos_enabled and (direction_mode != "auto_full" or fixed_direction is not None):
            _result = {
                "error_code": "INVALID_OOS_DIRECTION_OVERRIDE",
                "hint": "oos_enabled=true always uses train_fixed direction; fixed_direction is not allowed",
            }
            _result.update(await _record_mcp_experiment_failure(
                tool_name="run_backtest",
                task_id=task_id,
                expression=expression,
                params=task_params,
                status="rejected",
                failure_reason=_result["error_code"],
            ))
            return json.dumps(_result)
        if not oos_enabled and direction_mode == "auto_full" and fixed_direction is not None:
            _result = {
                "error_code": "INVALID_DIRECTION_POLICY",
                "hint": "fixed_direction must be null when direction_mode=auto_full",
            }
            _result.update(await _record_mcp_experiment_failure(
                tool_name="run_backtest",
                task_id=task_id,
                expression=expression,
                params=task_params,
                status="rejected",
                failure_reason=_result["error_code"],
            ))
            return json.dumps(_result)
        if not oos_enabled and direction_mode == "fixed" and fixed_direction not in (1, -1):
            _result = {
                "error_code": "INVALID_DIRECTION_POLICY",
                "hint": "direction_mode=fixed requires fixed_direction to be 1 or -1",
            }
            _result.update(await _record_mcp_experiment_failure(
                tool_name="run_backtest",
                task_id=task_id,
                expression=expression,
                params=task_params,
                status="rejected",
                failure_reason=_result["error_code"],
            ))
            return json.dumps(_result)

        logger.info(f"Getting universe: {universe}")
        await update_mcp_task_progress(
            task_id,
            status="fetching_data",
            progress=5,
            progress_message="fetching market data",
            stage="fetching_data",
        )
        try:
            market_df, stock_codes = await asyncio.to_thread(
                _fetch_data_for_market,
                universe,
                start_date,
                end_date,
                allow_remote_fetch,
                resolved_universe_date,
                lambda: _mcp_cancel_check(task_id),
                _mcp_fetch_progress_callback(
                    task_id,
                    status="fetching_data",
                    stage="fetching_data",
                    base_progress=5,
                    span=35,
                ),
            )
        except _RemotePrefetchRequired as exc:
            _result = exc.payload
            return await _record_market_data_prefetch_required(
                tool_name="run_backtest",
                task_id=task_id,
                expression=expression,
                params=task_params,
                exc=exc,
            )
        if market_df is None or len(market_df) == 0:
            _result = _market_data_unavailable_result(allow_remote_fetch)
            _result.update(await _record_mcp_experiment_failure(
                tool_name="run_backtest",
                task_id=task_id,
                expression=expression,
                params=task_params,
                status="data_quality_failed",
                failure_reason=_result["error_code"],
            ))
            return json.dumps(_result)
        data_provenance = _market_data_provenance_fields(
            market_df,
            universe=universe,
            start_date=start_date,
            end_date=end_date,
            universe_date=resolved_universe_date,
            stock_codes=stock_codes,
            endpoint="mcp.run_backtest",
        )
        task_params["data_snapshot_id"] = data_provenance["data_snapshot_id"]
        task_params["data_source"] = data_provenance.get("data_source")

        data_quality_report = None
        dq_enabled = oos_enabled if data_quality is None else data_quality
        if dq_enabled:
            await update_mcp_task_progress(
                task_id,
                status="checking_data_quality",
                progress=42,
                progress_message="checking data quality",
                stage="checking_data_quality",
            )
            _mcp_cancel_check(task_id)
            dq_config = DataQualityConfig(
                enabled=True,
                mode=data_quality_mode,
                max_abs_daily_ret=max_abs_daily_ret,
                max_missing_ratio_per_stock=max_missing_ratio_per_stock,
                adjustment=adjustment,
            )
            market_df, data_quality_report = await asyncio.to_thread(run_data_quality_gate, market_df, dq_config)
            _mcp_cancel_check(task_id)
        elif oos_enabled:
            data_quality_report = {
                "enabled": False,
                "data_quality_scope": "full_requested_sample",
                "issues": [],
                "warnings": ["OOS validation was run without the data-quality gate because data_quality=false"],
            }
        _annotate_data_provenance(data_quality_report, data_provenance)

        await update_mcp_task_progress(
            task_id,
            status="fetching_fundamentals",
            progress=50,
            progress_message="fetching fundamentals if required",
            stage="fetching_fundamentals",
        )
        market_df = await asyncio.to_thread(
            _enrich_with_fundamentals,
            expression,
            market_df,
            stock_codes,
            start_date,
            end_date,
            allow_remote_fetch,
            lambda: _mcp_cancel_check(task_id),
            _mcp_fetch_progress_callback(
                task_id,
                status="fetching_fundamentals",
                stage="fetching_fundamentals",
                base_progress=50,
                span=15,
            ),
        )

        logger.info(f"Running backtest: {expression}")
        await update_mcp_task_progress(
            task_id,
            status="backtesting",
            progress=68,
            progress_message="running factor backtest",
            stage="backtesting",
        )
        _mcp_cancel_check(task_id)
        executor = get_executor()
        if oos_enabled:
            oos_config = OOSConfig(rebalance_anchor=rebalance_anchor)
            future = executor.submit_cpu_work(
                _run_oos_backtest_in_process, market_df, expression, n_groups, holding_period,
                neutralize_industry=neutralize_industry, neutralize_cap=neutralize_cap,
                rebalance_anchor=rebalance_anchor, oos_config=oos_config, evaluation_stage=validation_stage,
            )
        else:
            future = executor.submit_cpu_work(
                _run_backtest_in_process, market_df, expression, n_groups, holding_period,
                neutralize_industry=neutralize_industry, neutralize_cap=neutralize_cap,
                rebalance_anchor=rebalance_anchor,
                direction_mode=direction_mode, fixed_direction=fixed_direction,
            )
        result = await _await_mcp_future_result(future, task_id, 600)
        _mcp_cancel_check(task_id)

        anti_overfit_result = None
        factor_df = result.get("_direction_adjusted_factor_df") if oos_enabled else result.get("_factor_df")
        if factor_df is not None and len(factor_df) > 100:
            try:
                await update_mcp_task_progress(
                    task_id,
                    status="analyzing",
                    progress=82,
                    progress_message="running anti-overfit diagnostics",
                    stage="analyzing",
                )
                _mcp_cancel_check(task_id)
                from .anti_overfit import run_anti_overfit as _run_ao
                anti_overfit_result = await asyncio.to_thread(_run_ao, factor_df, holding_period)
                _mcp_cancel_check(task_id)
                if oos_enabled and isinstance(anti_overfit_result, dict):
                    anti_overfit_result["diagnostic_scope"] = "direction_adjusted_oos_compat"
            except Exception as e:
                logger.warning(f"Anti-overfit analysis failed: {e}")

        bm_returns = None
        try:
            bm_returns = await asyncio.to_thread(
                _fetch_benchmark_for_market, benchmark, start_date, end_date, allow_remote_fetch
            )
        except Exception as e:
            logger.warning(f"Benchmark fetch failed: {e}")

        await update_mcp_task_progress(
            task_id,
            status="generating_report",
            progress=90,
            progress_message="generating report",
            stage="generating_report",
        )
        _mcp_cancel_check(task_id)
        report_result = await asyncio.to_thread(
            generate_report,
            result["ls_returns"],
            benchmark_returns=bm_returns,
            title=f"Factor: {expression}",
        )
        _mcp_cancel_check(task_id)

        backtest_summary = {
            "long_short_sharpe": result["long_short_sharpe"],
            "long_short_annual": result.get("long_short_annual", 0),
            "top_group_sharpe": result.get("top_group_sharpe", 0),
            "monotonicity_score": result["monotonicity_score"],
            "spread": result["spread"],
            "group_returns": result["group_returns"],
            "ic_mean": result.get("ic_mean", 0),
            "rank_ic_mean": result.get("rank_ic_mean", 0),
            "raw_ic_mean": result.get("raw_ic_mean", result.get("ic_mean", 0)),
            "raw_rank_ic_mean": result.get("raw_rank_ic_mean", result.get("rank_ic_mean", 0)),
            "direction_adjusted_ic_mean": result.get("direction_adjusted_ic_mean", result.get("ic_mean", 0)),
            "direction_adjusted_rank_ic_mean": result.get(
                "direction_adjusted_rank_ic_mean", result.get("rank_ic_mean", 0)
            ),
            "direction_mode": result.get("direction_mode", direction_mode),
            "direction_source": result.get("direction_source"),
            "direction_basis": result.get("direction_basis"),
            "fixed_direction": result.get("fixed_direction"),
            "direction_warning": result.get("direction_warning"),
            "flipped": result.get("flipped", False),
            "ic_ir": result.get("ic_ir", 0),
            "ic_win_rate": result.get("ic_win_rate", 0),
            "turnover": result.get("turnover", 0),
            "wq_fitness": result.get("wq_fitness", 0),
            "cost_adjusted": result.get("cost_adjusted", False),
            "cost_rate": result.get("cost_rate", 0),
            "total_cost_drag": result.get("total_cost_drag", 0),
        }
        if oos_enabled:
            backtest_summary["metrics_scope"] = result.get("report_scope", "oos_train_valid_selection")

        _result = {
            "report_path": report_result["report_path"],
            "metrics": report_result["metrics"],
            "backtest_summary": backtest_summary,
            "wq_brain": result.get("wq_brain", {}),
            "anti_overfit": anti_overfit_result,
            "params": {
                "expression": expression,
                "universe": universe,
                "start_date": start_date,
                "end_date": end_date,
                "universe_date": resolved_universe_date,
                "n_groups": n_groups,
                "holding_period": holding_period,
                "benchmark": benchmark,
                "neutralize_industry": neutralize_industry,
                "neutralize_cap": neutralize_cap,
                "rebalance_anchor": rebalance_anchor,
                "oos_enabled": oos_enabled,
                "validation_stage": validation_stage,
                "direction_mode": direction_mode,
                "fixed_direction": fixed_direction,
                "data_quality": data_quality,
                "allow_remote_fetch": allow_remote_fetch,
                "stock_count": len(stock_codes),
                "data_snapshot_id": data_provenance["data_snapshot_id"],
                "data_source": data_provenance.get("data_source"),
            },
        }
        _result.update(data_provenance)
        policy = _attach_policy_metadata(_result)
        promotion_blockers = (
            _oos_blockers_for_stage(validation_stage)
            if oos_enabled
            else _research_only_blockers(_result["params"])
        )
        _result["promotion_state"] = "research_only"
        _result["promotion_blockers"] = promotion_blockers
        _result["validation_provenance"] = research_only_provenance(
            source=f"mcp_run_backtest_oos_{validation_stage}" if oos_enabled else "mcp_run_backtest_auto_full",
            reason_code=promotion_blockers[0],
            blockers=promotion_blockers,
            params=_result["params"],
        )
        if data_quality_report is not None:
            _result["data_quality"] = data_quality_report
        if oos_enabled:
            public_oos = to_public_oos_result(result.get("oos_result", {}))
            public_oos["data_snapshot_id"] = data_provenance["data_snapshot_id"]
            public_oos["data_source"] = data_provenance.get("data_source")
            if data_quality_report is not None:
                public_oos["data_quality"] = data_quality_report
            if validation_stage == "selection":
                public_oos = withhold_final_test(public_oos)
            oos_scoring = _score_oos_for_stage(public_oos, data_quality_report, validation_stage)
            _result["oos_result"] = public_oos
            _result["direction_policy"] = "train_fixed"
            _result["final_test_policy"] = public_oos.get("final_test_policy", policy["final_test_policy"])
            _result["formal_safe"] = bool(policy["formal_safe"])
            _result["report_scope"] = public_oos.get("report_scope", result.get("report_scope"))
            _result["compatibility_warning"] = result.get("compatibility_warning")
            _result["scoring"] = oos_scoring
            if validation_stage == "selection":
                _result["selection_score"] = oos_scoring
            else:
                _result["oos_score"] = oos_scoring
        await _record_mcp_experiment_result(
            tool_name="run_backtest",
            task_id=task_id,
            expression=expression,
            payload=_result,
        )
        await update_mcp_task_progress(
            task_id,
            status="completed",
            progress=100,
            progress_message="run_backtest completed",
            stage="completed",
        )
        return json.dumps(_result, ensure_ascii=False, indent=2, default=str)

    except CancelledException:
        logger.info(f"Backtest task {task_id} cancelled")
        _error_msg = "cancelled"
        _result = _mcp_cancelled_result(task_id)
        await update_mcp_task_progress(
            task_id,
            status="cancelled",
            progress_message="run_backtest cancelled",
            stage="cancelled",
        )
        return json.dumps(_result, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Backtest failed: {traceback.format_exc()}")
        _error_msg = str(e)
        _result = {"error": str(e)}
        _result.update(await _record_mcp_experiment_failure(
            tool_name="run_backtest",
            task_id=task_id,
            expression=expression,
            params=task_params,
            status="rejected",
            failure_reason=str(e),
        ))
        return json.dumps(_result)
    finally:
        await complete_mcp_task(task_id, _result, _error_msg, expression)


@mcp.tool()
async def score_factor(
    expression: str,
    universe: str = "hs300",
    start_date: str = "2023-01-01",
    end_date: str = "2025-12-31",
    universe_date: str | None = None,
    n_groups: int = 5,
    holding_period: int = 5,
    benchmark: str = "hs300",
    neutralize_industry: bool = True,
    neutralize_cap: bool = True,
    rebalance_anchor: str | None = None,
    oos_enabled: bool = True,
    validation_stage: Literal["selection", "final"] = "selection",
    data_quality: bool | None = None,
    data_quality_mode: Literal["report_only", "filter", "strict"] = "filter",
    max_abs_daily_ret: float = 0.25,
    max_missing_ratio_per_stock: float = 0.2,
    adjustment: Literal["qfq", "hfq", "none", "unknown"] = "unknown",
    allow_remote_fetch: bool = False,
    submit_only: bool = False,
) -> str:
    """执行因子回测并返回综合评分(0-100)和等级(A/B/C/D)。

    比 run_backtest 更轻量,不生成 HTML 报告,专注评分。

    Args:
        expression: 因子表达式
        universe: 股票池 (small_scale/hs300/csi500/csi1000/csi2000)
        start_date: 起始日期 YYYY-MM-DD
        end_date: 结束日期 YYYY-MM-DD
        universe_date: 股票池成分股基准日期；默认使用 start_date
        n_groups: 分组数量
        holding_period: 持仓周期(交易日)
        benchmark: 基准 (hs300/zz500/sz50/csi1000)
        neutralize_industry: 行业中性化(默认开启)
        neutralize_cap: 市值中性化(默认开启)
        rebalance_anchor: 换仓网格锚定日期
        oos_enabled: 启用训练/验证/测试样本外评估；默认开启 OOS selection
        validation_stage: selection 只用 train+valid 选候选；final 才运行并暴露 test
        data_quality: 是否运行基础行情数据质量门；None 表示兼容默认
        allow_remote_fetch: 是否允许 MCP 工具在缓存缺失时阻塞式拉取远程行情；默认 False
        submit_only: 是否异步提交并立即返回 task_id；默认 False 保持同步行为

    Returns:
        JSON with score, grade, component_scores, key metrics.
    """
    from .iteration import compute_factor_score

    resolved_universe_date = _resolve_universe_date(universe_date, start_date)
    task_params = {
        "universe": universe, "start_date": start_date, "end_date": end_date,
        "universe_date": resolved_universe_date,
        "n_groups": n_groups, "holding_period": holding_period, "benchmark": benchmark,
        "rebalance_anchor": rebalance_anchor, "oos_enabled": oos_enabled, "validation_stage": validation_stage,
        "data_quality": data_quality, "allow_remote_fetch": allow_remote_fetch,
    }
    task_id = await start_mcp_task("score", expression, task_params)
    if submit_only:
        await update_mcp_task_progress(
            task_id,
            status="running",
            progress=0,
            progress_message="submitted score_factor",
            stage="submitted",
        )
        _submit_mcp_background_task(_run_mcp_tool_with_existing_task(
            task_id,
            lambda: score_factor(
                expression,
                universe=universe,
                start_date=start_date,
                end_date=end_date,
                universe_date=universe_date,
                n_groups=n_groups,
                holding_period=holding_period,
                benchmark=benchmark,
                neutralize_industry=neutralize_industry,
                neutralize_cap=neutralize_cap,
                rebalance_anchor=rebalance_anchor,
                oos_enabled=oos_enabled,
                validation_stage=validation_stage,
                data_quality=data_quality,
                data_quality_mode=data_quality_mode,
                max_abs_daily_ret=max_abs_daily_ret,
                max_missing_ratio_per_stock=max_missing_ratio_per_stock,
                adjustment=adjustment,
                allow_remote_fetch=allow_remote_fetch,
                submit_only=False,
            ),
        ))
        return _submitted_mcp_task_response(task_id)
    _error_msg = None
    _result = None
    try:
        await update_mcp_task_progress(
            task_id,
            status="validating",
            progress=2,
            progress_message="validating score_factor request",
            stage="validating",
        )
        _mcp_cancel_check(task_id)
        if validation_stage not in {"selection", "final"}:
            _result = {
                "error_code": "INVALID_VALIDATION_STAGE",
                "hint": "validation_stage must be selection or final",
            }
            _result.update(await _record_mcp_experiment_failure(
                tool_name="score_factor",
                task_id=task_id,
                expression=expression,
                params=task_params,
                status="rejected",
                failure_reason=_result["error_code"],
            ))
            return json.dumps(_result)
        if validation_stage == "final" and not oos_enabled:
            _result = {
                "error_code": "INVALID_VALIDATION_STAGE",
                "hint": "validation_stage=final requires oos_enabled=true",
            }
            _result.update(await _record_mcp_experiment_failure(
                tool_name="score_factor",
                task_id=task_id,
                expression=expression,
                params=task_params,
                status="rejected",
                failure_reason=_result["error_code"],
            ))
            return json.dumps(_result)
        try:
            await update_mcp_task_progress(
                task_id,
                status="fetching_data",
                progress=5,
                progress_message="fetching market data",
                stage="fetching_data",
            )
            market_df, stock_codes = await asyncio.to_thread(
                _fetch_data_for_market,
                universe,
                start_date,
                end_date,
                allow_remote_fetch,
                resolved_universe_date,
                lambda: _mcp_cancel_check(task_id),
                _mcp_fetch_progress_callback(
                    task_id,
                    status="fetching_data",
                    stage="fetching_data",
                    base_progress=5,
                    span=35,
                ),
            )
        except _RemotePrefetchRequired as exc:
            _result = exc.payload
            return await _record_market_data_prefetch_required(
                tool_name="score_factor",
                task_id=task_id,
                expression=expression,
                params=task_params,
                exc=exc,
            )
        if market_df is None or len(market_df) == 0:
            _result = _market_data_unavailable_result(allow_remote_fetch)
            _result.update(await _record_mcp_experiment_failure(
                tool_name="score_factor",
                task_id=task_id,
                expression=expression,
                params=task_params,
                status="data_quality_failed",
                failure_reason=_result["error_code"],
            ))
            return json.dumps(_result)
        data_provenance = _market_data_provenance_fields(
            market_df,
            universe=universe,
            start_date=start_date,
            end_date=end_date,
            universe_date=resolved_universe_date,
            stock_codes=stock_codes,
            endpoint="mcp.score_factor",
        )
        task_params["data_snapshot_id"] = data_provenance["data_snapshot_id"]
        task_params["data_source"] = data_provenance.get("data_source")

        data_quality_report = None
        dq_enabled = oos_enabled if data_quality is None else data_quality
        if dq_enabled:
            await update_mcp_task_progress(
                task_id,
                status="checking_data_quality",
                progress=42,
                progress_message="checking data quality",
                stage="checking_data_quality",
            )
            _mcp_cancel_check(task_id)
            dq_config = DataQualityConfig(
                enabled=True,
                mode=data_quality_mode,
                max_abs_daily_ret=max_abs_daily_ret,
                max_missing_ratio_per_stock=max_missing_ratio_per_stock,
                adjustment=adjustment,
            )
            market_df, data_quality_report = await asyncio.to_thread(run_data_quality_gate, market_df, dq_config)
            _mcp_cancel_check(task_id)
        elif oos_enabled:
            data_quality_report = {
                "enabled": False,
                "data_quality_scope": "full_requested_sample",
                "issues": [],
                "warnings": ["OOS validation was run without the data-quality gate because data_quality=false"],
            }
        _annotate_data_provenance(data_quality_report, data_provenance)

        await update_mcp_task_progress(
            task_id,
            status="fetching_fundamentals",
            progress=50,
            progress_message="fetching fundamentals if required",
            stage="fetching_fundamentals",
        )
        market_df = await asyncio.to_thread(
            _enrich_with_fundamentals,
            expression,
            market_df,
            stock_codes,
            start_date,
            end_date,
            allow_remote_fetch,
            lambda: _mcp_cancel_check(task_id),
            _mcp_fetch_progress_callback(
                task_id,
                status="fetching_fundamentals",
                stage="fetching_fundamentals",
                base_progress=50,
                span=15,
            ),
        )

        await update_mcp_task_progress(
            task_id,
            status="backtesting",
            progress=68,
            progress_message="running factor score backtest",
            stage="backtesting",
        )
        _mcp_cancel_check(task_id)
        executor = get_executor()
        if oos_enabled:
            oos_config = OOSConfig(rebalance_anchor=rebalance_anchor)
            future = executor.submit_cpu_work(
                _run_oos_backtest_in_process, market_df, expression, n_groups, holding_period,
                neutralize_industry=neutralize_industry, neutralize_cap=neutralize_cap,
                rebalance_anchor=rebalance_anchor, oos_config=oos_config, evaluation_stage=validation_stage,
            )
        else:
            future = executor.submit_cpu_work(
                _run_backtest_in_process, market_df, expression, n_groups, holding_period,
                neutralize_industry=neutralize_industry, neutralize_cap=neutralize_cap,
                rebalance_anchor=rebalance_anchor,
            )
        result = await _await_mcp_future_result(future, task_id, 600)
        _mcp_cancel_check(task_id)

        params = {
            "expression": expression,
            "universe": universe,
            "start_date": start_date,
            "end_date": end_date,
            "universe_date": resolved_universe_date,
            "n_groups": n_groups,
            "holding_period": holding_period,
            "benchmark": benchmark,
            "neutralize_industry": neutralize_industry,
            "neutralize_cap": neutralize_cap,
            "rebalance_anchor": rebalance_anchor,
            "oos_enabled": oos_enabled,
            "validation_stage": validation_stage,
            "data_quality": data_quality,
            "allow_remote_fetch": allow_remote_fetch,
            "stock_count": len(stock_codes),
            "data_snapshot_id": data_provenance["data_snapshot_id"],
            "data_source": data_provenance.get("data_source"),
        }
        await update_mcp_task_progress(
            task_id,
            status="analyzing",
            progress=85,
            progress_message="scoring factor",
            stage="analyzing",
        )

        if oos_enabled:
            public_oos = to_public_oos_result(result.get("oos_result", {}))
            if data_quality_report is not None:
                public_oos["data_quality"] = data_quality_report
            if validation_stage == "selection":
                public_oos = withhold_final_test(public_oos)
            oos_scoring = _score_oos_for_stage(public_oos, data_quality_report, validation_stage)
            metrics_key = "valid" if validation_stage == "selection" else "test"
            key_metrics = public_oos.get(metrics_key, {}).get("metrics", {})
            _result = {
                "score": oos_scoring["score"],
                "grade": oos_scoring["grade"],
                "decision": oos_scoring["decision"],
                "overfit_risk": oos_scoring["overfit_risk"],
                "key_metrics": {
                    "ic_mean": key_metrics.get("ic_mean", key_metrics.get("direction_adjusted_rank_ic_mean", 0)),
                    "ic_ir": key_metrics.get("ic_ir", 0),
                    "monotonicity": key_metrics.get("monotonicity_score", 0),
                    "top_group_sharpe": key_metrics.get("top_group_sharpe", 0),
                    "turnover": key_metrics.get("turnover", 0),
                    "wq_fitness": key_metrics.get("wq_fitness", 0),
                    "sharpe": key_metrics.get("sharpe", key_metrics.get("long_short_sharpe", 0)),
                    "max_drawdown": key_metrics.get("max_drawdown", 0),
                },
                "interpretation": {
                    "rating": oos_scoring["grade"],
                    "decision": oos_scoring["decision"],
                    "metrics_scope": oos_scoring["metrics_scope"],
                },
                "oos_score": oos_scoring,
                "oos_result": public_oos,
                "direction_policy": "train_fixed",
                "report_scope": public_oos.get("report_scope", result.get("report_scope")),
                "compatibility_warning": result.get("compatibility_warning"),
                "params": params,
            }
            _result.update(data_provenance)
            policy = _attach_policy_metadata(_result)
            component_scores = {
                "train": oos_scoring["train_score"],
                "valid": oos_scoring["valid_score"],
                "stability": oos_scoring["stability_score"],
                "decay_penalty": oos_scoring["decay_penalty"],
                "data_quality_penalty": oos_scoring["data_quality_penalty"],
            }
            if validation_stage == "final":
                component_scores["test"] = oos_scoring["test_score"]
            _result["component_scores"] = component_scores
            if validation_stage == "selection":
                _result["selection_score"] = oos_scoring
            else:
                _result["final_oos_score"] = oos_scoring
            _result["promotion_state"] = "research_only"
            _result["promotion_blockers"] = _oos_blockers_for_stage(validation_stage)
            _result["validation_provenance"] = research_only_provenance(
                source=f"mcp_score_factor_oos_{validation_stage}",
                reason_code=_result["promotion_blockers"][0],
                blockers=_result["promotion_blockers"],
                params=params,
            )
            if data_quality_report is not None:
                _result["data_quality"] = data_quality_report
            _result["oos_result"]["data_snapshot_id"] = data_provenance["data_snapshot_id"]
            _result["oos_result"]["data_source"] = data_provenance.get("data_source")
            _result["final_test_policy"] = public_oos.get("final_test_policy", policy["final_test_policy"])
            _result["formal_safe"] = bool(policy["formal_safe"])
            await _record_mcp_experiment_result(
                tool_name="score_factor",
                task_id=task_id,
                expression=expression,
                payload=_result,
            )
            await update_mcp_task_progress(
                task_id,
                status="completed",
                progress=100,
                progress_message="score_factor completed",
                stage="completed",
            )
            return json.dumps(_result, ensure_ascii=False, indent=2, default=str)

        bm_returns = None
        try:
            bm_returns = await asyncio.to_thread(
                _fetch_benchmark_for_market, benchmark, start_date, end_date, allow_remote_fetch
            )
        except Exception:
            pass

        await update_mcp_task_progress(
            task_id,
            status="generating_report",
            progress=90,
            progress_message="generating score report",
            stage="generating_report",
        )
        _mcp_cancel_check(task_id)
        report_result = await asyncio.to_thread(
            generate_report,
            result["ls_returns"],
            benchmark_returns=bm_returns,
            title="Factor Score",
        )
        _mcp_cancel_check(task_id)

        scoring = compute_factor_score(
            backtest_summary={
                "long_short_sharpe": result["long_short_sharpe"],
                "monotonicity_score": result["monotonicity_score"],
                "spread": result["spread"],
                "ic_mean": result.get("ic_mean", 0),
                "rank_ic_mean": result.get("rank_ic_mean", 0),
                "ic_ir": result.get("ic_ir", 0),
                "ic_win_rate": result.get("ic_win_rate", 0),
            },
            report_metrics=report_result["metrics"],
        )

        _result = {
            "score": scoring["score"],
            "grade": scoring["grade"],
            "component_scores": scoring["component_scores"],
            "key_metrics": {
                "ic_mean": result.get("ic_mean", 0),
                "ic_ir": result.get("ic_ir", 0),
                "monotonicity": result["monotonicity_score"],
                "top_group_sharpe": result.get("top_group_sharpe", 0),
                "turnover": result.get("turnover", 0),
                "wq_fitness": result.get("wq_fitness", 0),
                "sharpe": report_result["metrics"].get("sharpe", 0),
                "max_drawdown": report_result["metrics"].get("max_drawdown", 0),
            },
            "interpretation": {"rating": scoring["grade"]},
            "params": params,
        }
        _result.update(data_provenance)
        _attach_policy_metadata(_result)
        _result["promotion_state"] = "research_only"
        _result["promotion_blockers"] = _research_only_blockers(params)
        _result["validation_provenance"] = research_only_provenance(
            source="mcp_score_factor_auto_full",
            reason_code=AUTO_FULL_NOT_PROMOTABLE,
            blockers=_result["promotion_blockers"],
            params=params,
        )
        if data_quality_report is not None:
            _result["data_quality"] = data_quality_report
        await _record_mcp_experiment_result(
            tool_name="score_factor",
            task_id=task_id,
            expression=expression,
            payload=_result,
        )
        await update_mcp_task_progress(
            task_id,
            status="completed",
            progress=100,
            progress_message="score_factor completed",
            stage="completed",
        )
        return json.dumps(_result, ensure_ascii=False, indent=2, default=str)

    except CancelledException:
        logger.info(f"Score task {task_id} cancelled")
        _error_msg = "cancelled"
        _result = _mcp_cancelled_result(task_id)
        await update_mcp_task_progress(
            task_id,
            status="cancelled",
            progress_message="score_factor cancelled",
            stage="cancelled",
        )
        return json.dumps(_result, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Score failed: {traceback.format_exc()}")
        _error_msg = str(e)
        _result = {"error": str(e)}
        _result.update(await _record_mcp_experiment_failure(
            tool_name="score_factor",
            task_id=task_id,
            expression=expression,
            params=task_params,
            status="rejected",
            failure_reason=str(e),
        ))
        return json.dumps(_result)
    finally:
        await complete_mcp_task(task_id, _result, _error_msg, expression)


@mcp.tool()
async def list_experiments(
    status: str | None = None,
    universe: str | None = None,
    factor_hash: str | None = None,
    limit: int = 50,
) -> str:
    """查询实验 ledger，按状态、universe 或 factor_hash 过滤。"""
    try:
        factory = _get_ledger_session_factory()
        async with factory() as session:
            rows = await _ledger_list_experiments(
                session,
                status=status,
                universe=universe,
                factor_hash=factor_hash,
                limit=max(1, min(int(limit), 200)),
            )
            return json.dumps({"experiments": [_serialize_experiment(row) for row in rows]}, ensure_ascii=False, indent=2)
    except Exception as exc:
        return json.dumps({"error_code": "EXPERIMENT_LEDGER_UNAVAILABLE", "hint": str(exc)}, ensure_ascii=False)


@mcp.tool()
async def get_experiment(experiment_id: str) -> str:
    """返回单个实验 ledger 记录及其结果、artifact 和 promotion event。"""
    try:
        factory = _get_ledger_session_factory()
        async with factory() as session:
            row = await _ledger_get_experiment(session, experiment_id)
            if row is None:
                return json.dumps({"error_code": "EXPERIMENT_NOT_FOUND", "experiment_id": experiment_id}, ensure_ascii=False)
            return json.dumps(_serialize_experiment_detail(row), ensure_ascii=False, indent=2, default=str)
    except Exception as exc:
        return json.dumps({"error_code": "EXPERIMENT_LEDGER_UNAVAILABLE", "hint": str(exc)}, ensure_ascii=False)


@mcp.tool()
async def export_experiment_report(experiment_id: str) -> str:
    """导出单个实验的轻量 JSON/Markdown 报告，不包含大型 artifact 内容。"""
    try:
        factory = _get_ledger_session_factory()
        async with factory() as session:
            row = await _ledger_get_experiment(session, experiment_id)
            if row is None:
                return json.dumps({"error_code": "EXPERIMENT_NOT_FOUND", "experiment_id": experiment_id}, ensure_ascii=False)
            detail = _serialize_experiment_detail(row)
            markdown = [
                f"# Experiment {detail['experiment_id']}",
                "",
                f"- status: {detail.get('status')}",
                f"- factor_hash: {detail.get('factor_hash')}",
                f"- data_snapshot_id: {detail.get('data_snapshot_id')}",
                f"- direction_policy: {detail.get('direction_policy')}",
                f"- result_count: {len(detail.get('results') or [])}",
                f"- artifact_count: {len(detail.get('artifacts') or [])}",
                f"- promotion_event_count: {len(detail.get('promotion_events') or [])}",
                f"- export_event_count: {len(detail.get('export_events') or [])}",
            ]
            return json.dumps({
                "experiment": detail,
                "report_markdown": "\n".join(markdown),
            }, ensure_ascii=False, indent=2, default=str)
    except Exception as exc:
        return json.dumps({"error_code": "EXPERIMENT_LEDGER_UNAVAILABLE", "hint": str(exc)}, ensure_ascii=False)


@mcp.tool()
async def compare_experiments(left_experiment_id: str, right_experiment_id: str) -> str:
    """比较两个实验的核心 provenance 和 summary metrics。"""
    try:
        factory = _get_ledger_session_factory()
        async with factory() as session:
            left = await _ledger_get_experiment(session, left_experiment_id)
            right = await _ledger_get_experiment(session, right_experiment_id)
            if left is None or right is None:
                return json.dumps({
                    "error_code": "EXPERIMENT_NOT_FOUND",
                    "missing": [
                        exp_id for exp_id, row in ((left_experiment_id, left), (right_experiment_id, right)) if row is None
                    ],
                }, ensure_ascii=False)
            return json.dumps({
                "left": _serialize_experiment(left),
                "right": _serialize_experiment(right),
                "same_factor_hash": left.factor_hash == right.factor_hash,
                "same_universe": left.universe == right.universe,
                "same_data_snapshot": left.data_snapshot_id == right.data_snapshot_id,
                "status_transition": [left.status, right.status],
            }, ensure_ascii=False, indent=2, default=str)
    except Exception as exc:
        return json.dumps({"error_code": "EXPERIMENT_LEDGER_UNAVAILABLE", "hint": str(exc)}, ensure_ascii=False)


@mcp.tool()
async def show_factor_lineage(experiment_id: str) -> str:
    """显示实验 parent chain 和同 factor_hash 的历史尝试。"""
    try:
        factory = _get_ledger_session_factory()
        async with factory() as session:
            row = await _ledger_get_experiment(session, experiment_id)
            if row is None:
                return json.dumps({"error_code": "EXPERIMENT_NOT_FOUND", "experiment_id": experiment_id}, ensure_ascii=False)
            same_factor = await _ledger_list_experiments(session, factor_hash=row.factor_hash, limit=100)
            parent = await _ledger_get_experiment(session, row.parent_experiment_id) if row.parent_experiment_id else None
            return json.dumps({
                "experiment": _serialize_experiment(row),
                "parent": _serialize_experiment(parent) if parent is not None else None,
                "same_factor_attempts": [_serialize_experiment(item) for item in same_factor],
            }, ensure_ascii=False, indent=2, default=str)
    except Exception as exc:
        return json.dumps({"error_code": "EXPERIMENT_LEDGER_UNAVAILABLE", "hint": str(exc)}, ensure_ascii=False)


@mcp.tool()
async def summarize_trial_counts(
    universe: str | None = None,
    factor_hash: str | None = None,
    user_id: str | None = None,
) -> str:
    """汇总实验 ledger trial counts，供 multiple-testing gate 使用。"""
    try:
        factory = _get_ledger_session_factory()
        async with factory() as session:
            counts = await _ledger_summarize_trial_counts(
                session,
                user_id=user_id,
                universe=universe,
                factor_hash=factor_hash,
            )
            return json.dumps(counts, ensure_ascii=False, indent=2)
    except Exception as exc:
        return json.dumps({"error_code": "EXPERIMENT_LEDGER_UNAVAILABLE", "hint": str(exc)}, ensure_ascii=False)


@mcp.tool()
async def find_similar_factors(
    expression: str,
    limit: int = 20,
    threshold: float = 0.95,
) -> str:
    """按表达式 token/family 相似度查找可能重复的历史实验。"""
    try:
        factory = _get_ledger_session_factory()
        async with factory() as session:
            rows = await _ledger_list_experiments(session, limit=max(1, min(int(limit), 200)))
            matches = []
            for row in rows:
                report = _factor_similarity_report(expression, row.expression, threshold=threshold)
                if report["duplicated"] or report["same_family"]:
                    matches.append({
                        "experiment": _serialize_experiment(row),
                        "similarity": report,
                    })
            return json.dumps({"matches": matches}, ensure_ascii=False, indent=2, default=str)
    except Exception as exc:
        return json.dumps({"error_code": "EXPERIMENT_LEDGER_UNAVAILABLE", "hint": str(exc)}, ensure_ascii=False)


@mcp.tool()
async def run_multiple_testing_check(
    p_value: float,
    trial_counts: dict,
    alpha: float = 0.05,
    family_p_values: list[float] | None = None,
    experiment_id: str | None = None,
) -> str:
    """计算 trial-aware Bonferroni/FDR 检查，可选写回 experiment ledger。"""
    report = _multiple_testing_report(
        p_value=p_value,
        trial_counts=trial_counts,
        alpha=alpha,
        family_p_values=family_p_values,
    )
    if experiment_id:
        try:
            factory = _get_ledger_session_factory()
            async with factory() as session:
                row = await _ledger_get_experiment(session, experiment_id)
                if row is None:
                    report["ledger_warning"] = "EXPERIMENT_NOT_FOUND"
                else:
                    await _ledger_record_experiment_result(
                        session,
                        experiment_id=experiment_id,
                        stage="multiple_testing_checked",
                        metrics={"multiple_testing": report},
                    )
                    if row.status != "multiple_testing_checked":
                        try:
                            await _ledger_transition_status(session, experiment_id, "multiple_testing_checked")
                        except Exception as exc:
                            report["ledger_warning"] = str(exc)
                    await session.commit()
        except Exception as exc:
            report["ledger_warning"] = str(exc)
    return json.dumps(report, ensure_ascii=False, indent=2, default=str)


@mcp.tool()
async def promote_experiment(experiment_id: str, boundary: str = "candidate", provenance: dict | None = None) -> str:
    """运行 promotion provenance 检查并记录 promotion event。"""
    try:
        factory = _get_ledger_session_factory()
        async with factory() as session:
            row = await _ledger_get_experiment(session, experiment_id)
            if row is None:
                return json.dumps({"error_code": "EXPERIMENT_NOT_FOUND", "experiment_id": experiment_id}, ensure_ascii=False)
            decision = evaluate_promotion_provenance(provenance, boundary)
            await _ledger_record_promotion_event(
                session,
                experiment_id=experiment_id,
                boundary=boundary,
                decision="allowed" if decision["allowed"] else "blocked",
                blockers=decision["blockers"],
                provenance=provenance,
            )
            if decision["allowed"] and boundary == "candidate":
                await _ledger_transition_status(session, experiment_id, "candidate", promotion_stage=boundary)
            await session.commit()
            return json.dumps(decision, ensure_ascii=False, indent=2)
    except Exception as exc:
        return json.dumps({"error_code": "EXPERIMENT_LEDGER_UNAVAILABLE", "hint": str(exc)}, ensure_ascii=False)


@mcp.tool()
async def reject_experiment(experiment_id: str, reason: str) -> str:
    """将实验标记为 rejected，并保留结构化原因。"""
    try:
        factory = _get_ledger_session_factory()
        async with factory() as session:
            row = await _ledger_transition_status(session, experiment_id, "rejected", failure_reason=reason)
            await _ledger_record_promotion_event(
                session,
                experiment_id=experiment_id,
                boundary="manual_review",
                decision="rejected",
                blockers=[reason],
                provenance={"source": "mcp.reject_experiment"},
            )
            await session.commit()
            return json.dumps(_serialize_experiment(row), ensure_ascii=False, indent=2, default=str)
    except Exception as exc:
        return json.dumps({"error_code": "EXPERIMENT_LEDGER_UNAVAILABLE", "hint": str(exc)}, ensure_ascii=False)


@mcp.tool()
async def save_factor_pool_entry(
    expression: str,
    name: str | None = None,
    note: str | None = None,
    main_reason: str | None = None,
    tags: list[str] | None = None,
    category: str | None = None,
    pool_status: Literal["accepted", "watchlist", "rejected", "insufficient_data", "runtime_failed"] = "watchlist",
    factor_hash: str | None = None,
    experiment_id: str | None = None,
    task_id: str | None = None,
    market: str = "a_share",
    universe: str | None = None,
    holding_period: int | None = None,
    validation_stage: str | None = None,
    metrics: dict | None = None,
    backtest_summary: dict | None = None,
    params: dict | None = None,
    validation_provenance: dict | None = None,
    report_url: str | None = None,
    factor_card_path: str | None = None,
    entry_id: str | None = None,
) -> str:
    """保存或更新研究因子池条目；accepted 仅表示研究池状态，不触发 promotion。"""
    payload = _compact_factor_pool_payload(
        expression=expression,
        name=name,
        note=note,
        main_reason=main_reason,
        tags=tags,
        category=category,
        pool_status=pool_status,
        factor_hash=factor_hash,
        experiment_id=experiment_id,
        task_id=task_id,
        market=market,
        universe=universe,
        holding_period=holding_period,
        validation_stage=validation_stage,
        metrics=metrics,
        backtest_summary=backtest_summary,
        params=params,
        validation_provenance=validation_provenance,
        report_url=report_url,
        factor_card_path=factor_card_path,
        source="mcp",
        created_by="mcp.factor_pool",
    )
    try:
        factory = _get_ledger_session_factory()
        async with factory() as session:
            await ensure_mcp_system_user(session)
            row, created = await _pool_save_factor_pool_entry(
                session,
                owner_user_id=MCP_SYSTEM_USER_ID,
                entry_id=entry_id,
                data=payload,
            )
            await session.commit()
            await session.refresh(row)
            return json.dumps(
                {"entry": factor_pool_entry_to_dict(row), "created": created},
                ensure_ascii=False,
                indent=2,
                default=str,
            )
    except FactorPoolError as exc:
        return json.dumps({"error_code": "FACTOR_POOL_ERROR", "hint": str(exc)}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"error_code": "FACTOR_POOL_UNAVAILABLE", "hint": str(exc)}, ensure_ascii=False)


@mcp.tool()
async def list_factor_pool_entries(
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
) -> str:
    """按状态、category tag、tags、股票池、hash、experiment 或关键词查询研究因子池。"""
    try:
        factory = _get_ledger_session_factory()
        async with factory() as session:
            rows, total = await _pool_list_factor_pool_entries(
                session,
                owner_user_id=MCP_SYSTEM_USER_ID,
                pool_status=pool_status,
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
            return json.dumps(
                {
                    "entries": [factor_pool_entry_to_dict(row) for row in rows],
                    "total": total,
                    "limit": max(1, min(int(limit), 200)),
                    "offset": max(0, int(offset)),
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
    except FactorPoolError as exc:
        return json.dumps({"error_code": "FACTOR_POOL_ERROR", "hint": str(exc)}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"error_code": "FACTOR_POOL_UNAVAILABLE", "hint": str(exc)}, ensure_ascii=False)


@mcp.tool()
async def get_factor_pool_entry(entry_id: str) -> str:
    """查询单个研究因子池条目。"""
    try:
        factory = _get_ledger_session_factory()
        async with factory() as session:
            row = await _pool_get_factor_pool_entry(session, owner_user_id=MCP_SYSTEM_USER_ID, entry_id=entry_id)
            return json.dumps(factor_pool_entry_to_dict(row), ensure_ascii=False, indent=2, default=str)
    except FactorPoolError as exc:
        return json.dumps({"error_code": "FACTOR_POOL_ERROR", "hint": str(exc)}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"error_code": "FACTOR_POOL_UNAVAILABLE", "hint": str(exc)}, ensure_ascii=False)


@mcp.tool()
async def update_factor_pool_entry(
    entry_id: str,
    expression: str | None = None,
    name: str | None = None,
    note: str | None = None,
    main_reason: str | None = None,
    tags: list[str] | None = None,
    category: str | None = None,
    pool_status: str | None = None,
    factor_hash: str | None = None,
    experiment_id: str | None = None,
    task_id: str | None = None,
    market: str | None = None,
    universe: str | None = None,
    holding_period: int | None = None,
    validation_stage: str | None = None,
    metrics: dict | None = None,
    backtest_summary: dict | None = None,
    params: dict | None = None,
    validation_provenance: dict | None = None,
    report_url: str | None = None,
    factor_card_path: str | None = None,
) -> str:
    """更新研究因子池条目；状态变更不写 experiment ledger。"""
    payload = _compact_factor_pool_payload(
        expression=expression,
        name=name,
        note=note,
        main_reason=main_reason,
        tags=tags,
        category=category,
        pool_status=pool_status,
        factor_hash=factor_hash,
        experiment_id=experiment_id,
        task_id=task_id,
        market=market,
        universe=universe,
        holding_period=holding_period,
        validation_stage=validation_stage,
        metrics=metrics,
        backtest_summary=backtest_summary,
        params=params,
        validation_provenance=validation_provenance,
        report_url=report_url,
        factor_card_path=factor_card_path,
    )
    try:
        factory = _get_ledger_session_factory()
        async with factory() as session:
            row = await _pool_update_factor_pool_entry(
                session,
                owner_user_id=MCP_SYSTEM_USER_ID,
                entry_id=entry_id,
                data=payload,
            )
            await session.commit()
            await session.refresh(row)
            return json.dumps(factor_pool_entry_to_dict(row), ensure_ascii=False, indent=2, default=str)
    except FactorPoolError as exc:
        return json.dumps({"error_code": "FACTOR_POOL_ERROR", "hint": str(exc)}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"error_code": "FACTOR_POOL_UNAVAILABLE", "hint": str(exc)}, ensure_ascii=False)


@mcp.tool()
async def delete_factor_pool_entry(entry_id: str) -> str:
    """删除研究因子池条目。"""
    try:
        factory = _get_ledger_session_factory()
        async with factory() as session:
            await _pool_delete_factor_pool_entry(session, owner_user_id=MCP_SYSTEM_USER_ID, entry_id=entry_id)
            await session.commit()
            return json.dumps({"deleted": True, "entry_id": entry_id}, ensure_ascii=False, indent=2)
    except FactorPoolError as exc:
        return json.dumps({"error_code": "FACTOR_POOL_ERROR", "hint": str(exc)}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"error_code": "FACTOR_POOL_UNAVAILABLE", "hint": str(exc)}, ensure_ascii=False)


@mcp.tool()
async def list_factor_pool_tags(
    pool_status: str | None = None,
    universe: str | None = None,
    market: str | None = None,
) -> str:
    """查询研究因子池 tags、category 和 status facets。"""
    try:
        factory = _get_ledger_session_factory()
        async with factory() as session:
            facets = await _pool_list_factor_pool_tags(
                session,
                owner_user_id=MCP_SYSTEM_USER_ID,
                pool_status=pool_status,
                universe=universe,
                market=market,
            )
            return json.dumps(facets, ensure_ascii=False, indent=2, default=str)
    except FactorPoolError as exc:
        return json.dumps({"error_code": "FACTOR_POOL_ERROR", "hint": str(exc)}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"error_code": "FACTOR_POOL_UNAVAILABLE", "hint": str(exc)}, ensure_ascii=False)


def _compact_factor_pool_payload(**kwargs) -> dict:
    return {key: value for key, value in kwargs.items() if value is not None}


@mcp.tool()
def diagnose_factor(
    expression: str,
    ic_mean: float = 0.0,
    ic_ir: float = 0.0,
    monotonicity_score: float = 0.0,
    score: float = 50.0,
    experiment_id: str | None = None,
) -> str:
    """诊断因子问题并推荐突变策略。

    根据因子的 IC/IR/单调性/评分,判断失败模式(IC为零、IC为负、嵌套过深等),
    返回推荐的改进策略和定向 LLM 提示词。

    Args:
        expression: 当前因子表达式
        ic_mean: IC 均值
        ic_ir: IC 信息比率
        monotonicity_score: 分组单调性 (0-1)
        score: 综合评分 (0-100)

    Returns:
        JSON with diagnosis strategy, reason, and suggested mutation prompt.
    """
    from .mutation_engine import MutationEngine

    try:
        if experiment_id:
            exists = _experiment_exists_sync(experiment_id)
            if exists is False:
                return json.dumps(
                    {"error_code": "EXPERIMENT_NOT_FOUND", "experiment_id": experiment_id},
                    ensure_ascii=False,
                )

        engine = MutationEngine(
            expression=expression,
            metrics={
                "backtest_summary": {
                    "ic_mean": ic_mean,
                    "ic_ir": ic_ir,
                    "monotonicity_score": monotonicity_score,
                },
                "report_metrics": {},
            },
            score=score,
        )
        diagnosis = engine.diagnose_failure()
        sys_prompt, user_prompt = engine.build_mutation_prompt()

        output = {
            "strategy": diagnosis.strategy.value,
            "reason": diagnosis.reason,
            "details": diagnosis.details,
            "parent_experiment_id": experiment_id,
            "mutation_prompt": {
                "system": sys_prompt[:500] + "..." if len(sys_prompt) > 500 else sys_prompt,
                "user": user_prompt,
            },
            "params": {
                "source": "mcp.diagnose_factor",
                "parent_experiment_id": experiment_id,
                "ic_mean": ic_mean,
                "ic_ir": ic_ir,
                "monotonicity_score": monotonicity_score,
                "score": score,
            },
            "key_metrics": {
                "ic_mean": ic_mean,
                "ic_ir": ic_ir,
                "monotonicity_score": monotonicity_score,
                "score": score,
            },
            "artifact_type": "diagnosis",
            "artifact_uri": f"mcp://diagnose_factor/{experiment_id or 'standalone'}",
        }
        _run_ledger_sync(lambda: _record_mcp_experiment_result(
            tool_name="diagnose_factor",
            task_id=None,
            expression=expression,
            payload=output,
            status="parsed",
        ))
        return json.dumps(output, ensure_ascii=False, indent=2)

    except Exception as e:
        logger.error(f"Diagnose failed: {traceback.format_exc()}")
        return json.dumps({"error": str(e)})


@mcp.tool()
async def run_anti_overfit(
    expression: str,
    universe: str = "hs300",
    start_date: str = "2023-01-01",
    end_date: str = "2025-12-31",
    universe_date: str | None = None,
    holding_period: int = 5,
    neutralize_industry: bool = True,
    neutralize_cap: bool = True,
    allow_remote_fetch: bool = False,
    submit_only: bool = False,
) -> str:
    """对因子执行反过拟合检测(4项测试)。

    测试项: IC稳定性、子样本压力、安慰剂检验、半衰期估计。
    返回总分(0-100)和各测试通过情况。

    Args:
        expression: 因子表达式
        universe: 股票池 (small_scale/hs300/csi500/csi1000/csi2000)
        start_date: 起始日期 YYYY-MM-DD
        end_date: 结束日期 YYYY-MM-DD
        universe_date: 股票池成分股基准日期；默认使用 start_date
        holding_period: 持仓周期(交易日)
        neutralize_industry: 行业中性化(默认开启)
        neutralize_cap: 市值中性化(默认开启)
        allow_remote_fetch: 是否允许缓存缺失时阻塞式拉取远程行情；默认 False
        submit_only: 是否异步提交并立即返回 task_id；默认 False 保持同步行为

    Returns:
        JSON with score, recommendation, and per-test details.
    """
    from .anti_overfit import run_anti_overfit as _run_ao

    resolved_universe_date = _resolve_universe_date(universe_date, start_date)
    task_params = {
        "universe": universe, "start_date": start_date, "end_date": end_date,
        "universe_date": resolved_universe_date,
        "holding_period": holding_period,
        "neutralize_industry": neutralize_industry,
        "neutralize_cap": neutralize_cap,
        "oos_enabled": False,
        "direction_mode": "auto_full",
        "allow_remote_fetch": allow_remote_fetch,
    }
    task_id = await start_mcp_task("anti_overfit", expression, task_params)
    if submit_only:
        await update_mcp_task_progress(
            task_id,
            status="running",
            progress=0,
            progress_message="submitted run_anti_overfit",
            stage="submitted",
        )
        _submit_mcp_background_task(_run_mcp_tool_with_existing_task(
            task_id,
            lambda: run_anti_overfit(
                expression,
                universe=universe,
                start_date=start_date,
                end_date=end_date,
                universe_date=universe_date,
                holding_period=holding_period,
                neutralize_industry=neutralize_industry,
                neutralize_cap=neutralize_cap,
                allow_remote_fetch=allow_remote_fetch,
                submit_only=False,
            ),
        ))
        return _submitted_mcp_task_response(task_id)
    _error_msg = None
    _result = None
    try:
        await update_mcp_task_progress(
            task_id,
            status="validating",
            progress=2,
            progress_message="validating run_anti_overfit request",
            stage="validating",
        )
        _mcp_cancel_check(task_id)
        try:
            await update_mcp_task_progress(
                task_id,
                status="fetching_data",
                progress=5,
                progress_message="fetching market data",
                stage="fetching_data",
            )
            market_df, stock_codes = await asyncio.to_thread(
                _fetch_data_for_market,
                universe,
                start_date,
                end_date,
                allow_remote_fetch,
                resolved_universe_date,
                lambda: _mcp_cancel_check(task_id),
                _mcp_fetch_progress_callback(
                    task_id,
                    status="fetching_data",
                    stage="fetching_data",
                    base_progress=5,
                    span=40,
                ),
            )
        except _RemotePrefetchRequired as exc:
            _result = exc.payload
            return await _record_market_data_prefetch_required(
                tool_name="run_anti_overfit",
                task_id=task_id,
                expression=expression,
                params=task_params,
                exc=exc,
            )
        if market_df is None or len(market_df) == 0:
            _result = _market_data_unavailable_result(allow_remote_fetch)
            _result.update(await _record_mcp_experiment_failure(
                tool_name="run_anti_overfit",
                task_id=task_id,
                expression=expression,
                params=task_params,
                status="data_quality_failed",
                failure_reason=_result["error_code"],
            ))
            return json.dumps(_result)
        data_provenance = _market_data_provenance_fields(
            market_df,
            universe=universe,
            start_date=start_date,
            end_date=end_date,
            universe_date=resolved_universe_date,
            stock_codes=stock_codes,
            endpoint="mcp.run_anti_overfit",
        )
        task_params["data_snapshot_id"] = data_provenance["data_snapshot_id"]
        task_params["data_source"] = data_provenance.get("data_source")

        await update_mcp_task_progress(
            task_id,
            status="fetching_fundamentals",
            progress=50,
            progress_message="fetching fundamentals if required",
            stage="fetching_fundamentals",
        )
        market_df = await asyncio.to_thread(
            _enrich_with_fundamentals,
            expression,
            market_df,
            stock_codes,
            start_date,
            end_date,
            allow_remote_fetch,
            lambda: _mcp_cancel_check(task_id),
            _mcp_fetch_progress_callback(
                task_id,
                status="fetching_fundamentals",
                stage="fetching_fundamentals",
                base_progress=50,
                span=15,
            ),
        )

        await update_mcp_task_progress(
            task_id,
            status="backtesting",
            progress=68,
            progress_message="computing factor data for anti-overfit",
            stage="backtesting",
        )
        _mcp_cancel_check(task_id)
        executor = get_executor()
        future = executor.submit_cpu_work(
            _run_backtest_in_process, market_df, expression, 5, holding_period,
            cost_rate=0,
            neutralize_industry=neutralize_industry, neutralize_cap=neutralize_cap,
        )
        result = await _await_mcp_future_result(future, task_id, 600)
        _mcp_cancel_check(task_id)
        factor_df = result.get("_factor_df")
        if factor_df is None or len(factor_df) < 100:
            _result = {"error": "Insufficient factor data for anti-overfit analysis."}
            _result.update(await _record_mcp_experiment_failure(
                tool_name="run_anti_overfit",
                task_id=task_id,
                expression=expression,
                params=task_params,
                status="rejected",
                failure_reason=_result["error"],
            ))
            return json.dumps(_result)

        await update_mcp_task_progress(
            task_id,
            status="analyzing",
            progress=85,
            progress_message="running anti-overfit checks",
            stage="analyzing",
        )
        _mcp_cancel_check(task_id)
        _result = dict(await asyncio.to_thread(_run_ao, factor_df, holding_period))
        _mcp_cancel_check(task_id)
        _result["params"] = task_params
        _result.update(data_provenance)
        _result["artifact_type"] = "anti_overfit"
        _result["artifact_uri"] = f"mcp://run_anti_overfit/{task_id}"
        _attach_policy_metadata(_result)
        await _record_mcp_experiment_result(
            tool_name="run_anti_overfit",
            task_id=task_id,
            expression=expression,
            payload=_result,
            status="anti_overfit_checked",
        )
        await update_mcp_task_progress(
            task_id,
            status="completed",
            progress=100,
            progress_message="run_anti_overfit completed",
            stage="completed",
        )
        return json.dumps(_result, ensure_ascii=False, indent=2, default=str)

    except CancelledException:
        logger.info(f"Anti-overfit task {task_id} cancelled")
        _error_msg = "cancelled"
        _result = _mcp_cancelled_result(task_id)
        await update_mcp_task_progress(
            task_id,
            status="cancelled",
            progress_message="run_anti_overfit cancelled",
            stage="cancelled",
        )
        return json.dumps(_result, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Anti-overfit failed: {traceback.format_exc()}")
        _error_msg = str(e)
        _result = {"error": str(e)}
        _result.update(await _record_mcp_experiment_failure(
            tool_name="run_anti_overfit",
            task_id=task_id,
            expression=expression,
            params=task_params,
            status="rejected",
            failure_reason=str(e),
        ))
        return json.dumps(_result)
    finally:
        await complete_mcp_task(task_id, _result, _error_msg, expression)


@mcp.tool()
async def run_rolling_validation(
    expression: str,
    universe: str = "hs300",
    start_date: str = "2020-01-01",
    end_date: str = "2025-12-31",
    universe_date: str | None = None,
    holding_period: int = 5,
    neutralize_industry: bool = True,
    neutralize_cap: bool = True,
    allow_remote_fetch: bool = False,
    submit_only: bool = False,
) -> str:
    """对因子执行滚动验证(Walk-Forward)。

    将数据切分为多个 训练/验证/测试 窗口(默认 3年/1年/1年,步长3个月),
    计算每个窗口的 IC/IR,评估因子在样本外的衰减情况。

    Args:
        expression: 因子表达式
        universe: 股票池 (small_scale/hs300/csi500/csi1000/csi2000)
        start_date: 起始日期(建议≥5年数据)
        end_date: 结束日期
        universe_date: 股票池成分股基准日期；默认使用 start_date
        holding_period: 持仓周期(交易日)
        neutralize_industry: 行业中性化(默认开启)
        neutralize_cap: 市值中性化(默认开启)
        allow_remote_fetch: 是否允许缓存缺失时阻塞式拉取远程行情；默认 False
        submit_only: 是否异步提交并立即返回 task_id；默认 False 保持同步行为

    Returns:
        JSON with composite score, per-window results, decay analysis.
    """
    from .rolling_validator import run_rolling_validation as _run_rv

    resolved_universe_date = _resolve_universe_date(universe_date, start_date)
    task_params = {
        "universe": universe, "start_date": start_date, "end_date": end_date,
        "universe_date": resolved_universe_date,
        "holding_period": holding_period,
        "neutralize_industry": neutralize_industry,
        "neutralize_cap": neutralize_cap,
        "oos_enabled": False,
        "direction_mode": "auto_full",
        "allow_remote_fetch": allow_remote_fetch,
    }
    task_id = await start_mcp_task("rolling_validation", expression, task_params)
    if submit_only:
        await update_mcp_task_progress(
            task_id,
            status="running",
            progress=0,
            progress_message="submitted run_rolling_validation",
            stage="submitted",
        )
        _submit_mcp_background_task(_run_mcp_tool_with_existing_task(
            task_id,
            lambda: run_rolling_validation(
                expression,
                universe=universe,
                start_date=start_date,
                end_date=end_date,
                universe_date=universe_date,
                holding_period=holding_period,
                neutralize_industry=neutralize_industry,
                neutralize_cap=neutralize_cap,
                allow_remote_fetch=allow_remote_fetch,
                submit_only=False,
            ),
        ))
        return _submitted_mcp_task_response(task_id)
    _error_msg = None
    _result = None
    try:
        await update_mcp_task_progress(
            task_id,
            status="validating",
            progress=2,
            progress_message="validating run_rolling_validation request",
            stage="validating",
        )
        _mcp_cancel_check(task_id)
        try:
            await update_mcp_task_progress(
                task_id,
                status="fetching_data",
                progress=5,
                progress_message="fetching market data",
                stage="fetching_data",
            )
            market_df, stock_codes = await asyncio.to_thread(
                _fetch_data_for_market,
                universe,
                start_date,
                end_date,
                allow_remote_fetch,
                resolved_universe_date,
                lambda: _mcp_cancel_check(task_id),
                _mcp_fetch_progress_callback(
                    task_id,
                    status="fetching_data",
                    stage="fetching_data",
                    base_progress=5,
                    span=40,
                ),
            )
        except _RemotePrefetchRequired as exc:
            _result = exc.payload
            return await _record_market_data_prefetch_required(
                tool_name="run_rolling_validation",
                task_id=task_id,
                expression=expression,
                params=task_params,
                exc=exc,
            )
        if market_df is None or len(market_df) == 0:
            _result = _market_data_unavailable_result(allow_remote_fetch)
            _result.update(await _record_mcp_experiment_failure(
                tool_name="run_rolling_validation",
                task_id=task_id,
                expression=expression,
                params=task_params,
                status="data_quality_failed",
                failure_reason=_result["error_code"],
            ))
            return json.dumps(_result)
        data_provenance = _market_data_provenance_fields(
            market_df,
            universe=universe,
            start_date=start_date,
            end_date=end_date,
            universe_date=resolved_universe_date,
            stock_codes=stock_codes,
            endpoint="mcp.run_rolling_validation",
        )
        task_params["data_snapshot_id"] = data_provenance["data_snapshot_id"]
        task_params["data_source"] = data_provenance.get("data_source")

        await update_mcp_task_progress(
            task_id,
            status="fetching_fundamentals",
            progress=50,
            progress_message="fetching fundamentals if required",
            stage="fetching_fundamentals",
        )
        market_df = await asyncio.to_thread(
            _enrich_with_fundamentals,
            expression,
            market_df,
            stock_codes,
            start_date,
            end_date,
            allow_remote_fetch,
            lambda: _mcp_cancel_check(task_id),
            _mcp_fetch_progress_callback(
                task_id,
                status="fetching_fundamentals",
                stage="fetching_fundamentals",
                base_progress=50,
                span=15,
            ),
        )

        await update_mcp_task_progress(
            task_id,
            status="backtesting",
            progress=68,
            progress_message="computing factor data for rolling validation",
            stage="backtesting",
        )
        _mcp_cancel_check(task_id)
        executor = get_executor()
        future = executor.submit_cpu_work(
            _run_backtest_in_process, market_df, expression, 5, holding_period,
            cost_rate=0,
            neutralize_industry=neutralize_industry, neutralize_cap=neutralize_cap,
        )
        result = await _await_mcp_future_result(future, task_id, 600)
        _mcp_cancel_check(task_id)
        factor_df = result.get("_factor_df")
        if factor_df is None or len(factor_df) < 100:
            _result = {"error": "Insufficient factor data for rolling validation."}
            _result.update(await _record_mcp_experiment_failure(
                tool_name="run_rolling_validation",
                task_id=task_id,
                expression=expression,
                params=task_params,
                status="rejected",
                failure_reason=_result["error"],
            ))
            return json.dumps(_result)

        await update_mcp_task_progress(
            task_id,
            status="analyzing",
            progress=85,
            progress_message="running rolling validation",
            stage="analyzing",
        )
        _mcp_cancel_check(task_id)
        _result = dict(await asyncio.to_thread(_run_rv, factor_df, holding_period))
        _mcp_cancel_check(task_id)
        _result["params"] = task_params
        _result.update(data_provenance)
        _result["artifact_type"] = "rolling_validation"
        _result["artifact_uri"] = f"mcp://run_rolling_validation/{task_id}"
        _attach_policy_metadata(_result)
        await _record_mcp_experiment_result(
            tool_name="run_rolling_validation",
            task_id=task_id,
            expression=expression,
            payload=_result,
            status="rolling_checked",
        )
        await update_mcp_task_progress(
            task_id,
            status="completed",
            progress=100,
            progress_message="run_rolling_validation completed",
            stage="completed",
        )
        return json.dumps(_result, ensure_ascii=False, indent=2, default=str)

    except CancelledException:
        logger.info(f"Rolling validation task {task_id} cancelled")
        _error_msg = "cancelled"
        _result = _mcp_cancelled_result(task_id)
        await update_mcp_task_progress(
            task_id,
            status="cancelled",
            progress_message="run_rolling_validation cancelled",
            stage="cancelled",
        )
        return json.dumps(_result, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Rolling validation failed: {traceback.format_exc()}")
        _error_msg = str(e)
        _result = {"error": str(e)}
        _result.update(await _record_mcp_experiment_failure(
            tool_name="run_rolling_validation",
            task_id=task_id,
            expression=expression,
            params=task_params,
            status="rejected",
            failure_reason=str(e),
        ))
        return json.dumps(_result)
    finally:
        await complete_mcp_task(task_id, _result, _error_msg, expression)


@mcp.tool()
async def wq_brain_submit(
    expression: str,
    tag: str,
    region: str = "USA",
    universe: str = "TOP3000",
    delay: int = 1,
    decay: int = 0,
    neutralization: str = "SUBINDUSTRY",
    truncation: float = 0.08,
    auto_submit: bool = False,
    submission_override_reason: str | None = None,
) -> str:
    """提交因子表达式到 WorldQuant BRAIN 平台进行真实模拟。

    与 run_backtest（本地 A 股回测）不同，此工具调用 WQ BRAIN 真实 API，
    在美股 TOP3000 等市场上评估因子。返回样本内/样本外指标和提交资格。

    需要在 .env 中配置 WQ_BRAIN_EMAIL 和 WQ_BRAIN_PASSWORD。

    Args:
        expression: FASTEXPR 表达式 (如 "rank(close/open)")
        tag: 提交者标识 (如 "agent-lowcorr-0506")，用于追踪哪个 agent 提交
        region: 市场区域 (当前仅 USA 可用)
        universe: WQ Universe (TOP3000, TOP500 等)
        delay: 信号延迟 (0 或 1)
        decay: Alpha 衰减 (0-20)
        neutralization: 中性化 (SUBINDUSTRY, INDUSTRY, SECTOR, MARKET, NONE)
        truncation: 权重截断 (0-0.5)
        auto_submit: 如果检查全部通过，自动提交到 WQ 审核
        submission_override_reason: 本地 OOS/data_quality preflight 不可用时的显式提交豁免理由

    Returns:
        JSON with IS/OOS metrics, alpha_id, checks, submittable status.
    """
    from .wq_brain_client import get_client
    from .wq_brain_client import is_configured as _wq_configured

    task_id = await start_mcp_task("wq_brain_submit", expression, {
        "expression": expression, "tag": tag, "region": region, "universe": universe,
        "delay": delay, "decay": decay, "neutralization": neutralization,
        "truncation": truncation, "auto_submit": auto_submit,
        "submission_override_reason": submission_override_reason,
    })
    _error_msg = None
    _result = None
    try:
        if not _wq_configured():
            return json.dumps({"error": "WQ BRAIN 未配置 — 请设置 WQ_BRAIN_EMAIL 和 WQ_BRAIN_PASSWORD"})

        client = get_client("primary")
        authenticated = await asyncio.to_thread(client.authenticate)
        if not authenticated:
            return json.dumps({"error": "WQ BRAIN 认证失败"})

        _result = await asyncio.to_thread(
            run_single_simulation, client,
            expression, region=region, universe=universe,
            delay=delay, decay=decay, neutralization=neutralization,
            truncation=truncation, auto_submit=auto_submit, tag=tag,
            submission_override_reason=submission_override_reason,
        )
        await asyncio.to_thread(client.close)

        if not _result.get("ok"):
            _error_msg = _result.get("error", "Simulation failed")
            return json.dumps({"error": _error_msg})

        return json.dumps(_result, ensure_ascii=False, indent=2, default=str)

    except Exception as e:
        logger.error(f"WQ BRAIN submit failed: {traceback.format_exc()}")
        _error_msg = str(e)
        return json.dumps({"error": str(e)})
    finally:
        await complete_mcp_task(task_id, _result, _error_msg, expression)


@mcp.tool()
async def wq_brain_batch_submit(
    expression: str,
    tag: str,
    regions: list[str] | None = None,
    delays: list[int] | None = None,
    universes: list[str] | None = None,
    neutralizations: list[str] | None = None,
    decay: int = 0,
    truncation: float = 0.08,
    auto_submit: bool = False,
    submission_override_reason: str | None = None,
) -> str:
    """批量扫描因子表达式在多个参数组合下的 WQ BRAIN 表现。

    在 region × delay × universe × neutralization 的网格上逐一模拟，
    返回每个组合的 IS 指标和最优组合。适合找出同一表达式的最佳参数。

    Args:
        expression: FASTEXPR 表达式
        tag: 提交者标识 (如 "agent-lowcorr-0506")，用于追踪哪个 agent 提交
        regions: 市场区域列表 (默认 ["USA"])
        delays: 信号延迟列表 (默认 [1])
        universes: Universe 列表 (默认 ["TOP3000"])
        neutralizations: 中性化列表 (默认 ["SUBINDUSTRY"])
        decay: Alpha 衰减 (0-20, 共用)
        truncation: 权重截断 (0-0.5, 共用)
        auto_submit: 全部检查通过时自动提交
        submission_override_reason: 本地 OOS/data_quality preflight 不可用时的显式提交豁免理由

    Returns:
        JSON with per-combination results, best_fitness, submittable_count.
    """
    from .wq_brain_client import get_client
    from .wq_brain_client import is_configured as _wq_configured

    regions = regions or ["USA"]
    delays = delays or [1]
    universes = universes or ["TOP3000"]
    neutralizations = neutralizations or ["SUBINDUSTRY"]

    task_id = await start_mcp_task("wq_brain_batch", expression, {
        "expression": expression, "tag": tag,
        "regions": regions, "delays": delays, "universes": universes,
        "neutralizations": neutralizations, "decay": decay, "truncation": truncation,
        "auto_submit": auto_submit,
        "submission_override_reason": submission_override_reason,
    })
    _error_msg = None
    _result = None
    try:
        if not _wq_configured():
            return json.dumps({"error": "WQ BRAIN 未配置 — 请设置 WQ_BRAIN_EMAIL 和 WQ_BRAIN_PASSWORD"})

        total = len(regions) * len(delays) * len(universes) * len(neutralizations)
        if total > 36:
            return json.dumps({"error": f"组合数 {total} 超过上限 36"})

        client = get_client("primary")
        authenticated = await asyncio.to_thread(client.authenticate)
        if not authenticated:
            return json.dumps({"error": "WQ BRAIN 认证失败"})

        _result = await asyncio.to_thread(
            run_batch_simulation, client, expression,
            regions=regions, delays=delays, universes=universes,
            neutralizations=neutralizations, decay=decay, truncation=truncation,
            auto_submit=auto_submit, tag=tag,
            submission_override_reason=submission_override_reason,
        )
        await asyncio.to_thread(client.close)

        if not _result.get("ok"):
            _error_msg = _result.get("error")
        return json.dumps(_result, ensure_ascii=False, indent=2, default=str)

    except Exception as e:
        logger.error(f"WQ BRAIN batch failed: {traceback.format_exc()}")
        _error_msg = str(e)
        return json.dumps({"error": str(e)})
    finally:
        await complete_mcp_task(task_id, _result, _error_msg, expression)


@mcp.tool()
async def wq_brain_submit_by_ids(
    alpha_ids: list[str],
    account: str = "primary",
    expressions_by_alpha_id: dict[str, str] | None = None,
    submission_override_reason: str | None = None,
) -> str:
    """批量提交已模拟的 alpha（通过 alpha_id 直接提交，无需重新模拟）。

    用于提交之前模拟过但未正式提交的 A 级 alpha。逐个处理，
    每个 alpha 等待 SC 检查结果（最长 120s）。

    Args:
        alpha_ids: 要提交的 alpha_id 列表（最多 50 个）
        account: WQ 账号（提交只能用 'primary'）
        expressions_by_alpha_id: 可选的 alpha_id 到表达式映射，用于本地 OOS/data_quality preflight
        submission_override_reason: 缺少本地表达式溯源或 preflight 不通过时的显式提交豁免理由

    Returns:
        JSON with per-alpha result (ACTIVE/SC_FAIL/TIMEOUT) and summary.
    """
    from .wq_brain_client import get_client
    from .wq_brain_client import is_configured as _wq_configured

    if account != "primary":
        return json.dumps({"error": "Alpha 提交仅允许 primary 账号"})
    if not _wq_configured(account):
        return json.dumps({"error": "WQ BRAIN 未配置"})
    if len(alpha_ids) > 50:
        return json.dumps({"error": f"alpha_ids 数量 {len(alpha_ids)} 超过上限 50"})

    task_id = await start_mcp_task(
        "wq_brain_submit_by_ids",
        None,
        {
            "alpha_ids": alpha_ids,
            "account": account,
            "has_expression_provenance": bool(expressions_by_alpha_id),
            "submission_override_reason": submission_override_reason,
        },
    )
    _result = None
    _error_msg = None

    try:
        client = get_client(account)
        authenticated = await asyncio.to_thread(client.authenticate)
        if not authenticated:
            _error_msg = "WQ BRAIN 认证失败"
            return json.dumps({"error": _error_msg})

        expressions_by_alpha_id = expressions_by_alpha_id or {}
        preflight_cache: dict[str, dict] = {}

        def _preflight_for_alpha(alpha_id: str) -> dict:
            expression = expressions_by_alpha_id.get(alpha_id)
            cache_key = expression or f"missing:{alpha_id}"
            if cache_key not in preflight_cache:
                preflight_cache[cache_key] = require_submission_preflight(
                    expression,
                    override_reason=submission_override_reason,
                    unavailable_reason=(
                        f"Alpha {alpha_id} has no local expression provenance for submission preflight"
                    ),
                    target_scope=wq_target_scope(),
                )
            return preflight_cache[cache_key]

        _result = await asyncio.to_thread(
            run_submit_by_ids,
            client,
            alpha_ids,
            submission_preflight_lookup=_preflight_for_alpha,
        )
        await asyncio.to_thread(client.close)

        return json.dumps(_result, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        _error_msg = str(e)
        return json.dumps({"error": _error_msg})
    finally:
        await complete_mcp_task(task_id, _result, _error_msg)


@mcp.tool()
async def wq_brain_list_alphas(
    account: str = "primary",
    limit: int = 100,
    offset: int = 0,
    min_fitness: float | None = None,
    status_filter: str | None = None,
) -> str:
    """列出 WQ BRAIN 平台上的所有 alpha（包括已模拟未提交的）。

    可按 fitness 下限和状态过滤。返回 alpha_id、表达式、指标。

    Args:
        account: WQ 账号 ('primary' 或 'alt')
        limit: 返回数量上限（最大 100）
        offset: 分页偏移
        min_fitness: 最低 fitness 过滤（如 1.0 只看 A 级）
        status_filter: 状态过滤（如 'UNSUBMITTED' 或 'ACTIVE'）

    Returns:
        JSON with alpha list, each containing alpha_id, expression, metrics.
    """
    from .wq_brain_client import get_client
    from .wq_brain_client import is_configured as _wq_configured

    if not _wq_configured(account):
        return json.dumps({"error": f"WQ BRAIN 未配置 (account={account})"})

    client = get_client(account)
    authenticated = await asyncio.to_thread(client.authenticate)
    if not authenticated:
        return json.dumps({"error": "WQ BRAIN 认证失败"})

    result = await asyncio.to_thread(
        run_list_alphas, client,
        limit=limit, offset=offset,
        min_fitness=min_fitness, status_filter=status_filter,
    )
    await asyncio.to_thread(client.close)

    if not result.get("ok"):
        return json.dumps({"error": result.get("error", "unknown")})

    return json.dumps(result, ensure_ascii=False, indent=2, default=str)


@mcp.tool()
async def wq_brain_check_alphas(
    alpha_ids: list[str],
    account: str = "primary",
) -> str:
    """批量查询 alpha 在 WQ BRAIN 平台上的状态。

    返回每个 alpha 的状态（ACTIVE/UNSUBMITTED）、SC 检查结果、指标。

    Args:
        alpha_ids: 要查询的 alpha_id 列表（最多 50 个）
        account: WQ 账号 ('primary' 或 'alt')

    Returns:
        JSON with summary and per-alpha status.
    """
    from .wq_brain_client import get_client
    from .wq_brain_client import is_configured as _wq_configured

    if not _wq_configured(account):
        return json.dumps({"error": f"WQ BRAIN 未配置 (account={account})"})
    if len(alpha_ids) > 50:
        return json.dumps({"error": f"alpha_ids 数量 {len(alpha_ids)} 超过上限 50"})

    client = get_client(account)
    authenticated = await asyncio.to_thread(client.authenticate)
    if not authenticated:
        return json.dumps({"error": "WQ BRAIN 认证失败"})

    result = await asyncio.to_thread(run_check_alphas, client, alpha_ids)
    await asyncio.to_thread(client.close)

    return json.dumps(result, ensure_ascii=False, indent=2, default=str)


@mcp.tool()
async def wq_brain_finalize_submissions(
    alpha_ids: list[str],
    account: str = "primary",
) -> str:
    """查询已提交 alpha 的最终 SC 检查结果。

    提交 alpha 后 SC 检查可能需要数小时。初次提交 SC 超时的 alpha 用此工具查询最终结果。
    会自动更新 DB 中已解决 alpha 的状态（ACTIVE / SC_FAIL）。

    Args:
        alpha_ids: 要查询最终状态的 alpha_id 列表（最多 100 个）
        account: WQ 账号 ('primary' 或 'alt')

    Returns:
        JSON: per-alpha final_status (ACTIVE/SC_FAIL/SC_PENDING/ERROR) + summary
    """
    task_id = await start_mcp_task("wq_brain_finalize", None, {
        "alpha_ids": alpha_ids[:10], "account": account, "total": len(alpha_ids),
    })
    _error_msg = None
    _result = None
    try:
        from .routes.wq_brain_batch import _finalize_alpha_statuses
        from .wq_brain_client import get_client
        from .wq_brain_client import is_configured as _wq_configured

        if not _wq_configured(account):
            return json.dumps({"error": f"WQ BRAIN 未配置 (account={account})"})
        if len(alpha_ids) > 100:
            return json.dumps({"error": f"alpha_ids 数量 {len(alpha_ids)} 超过上限 100"})

        client = get_client(account)
        authenticated = await asyncio.to_thread(client.authenticate)
        if not authenticated:
            return json.dumps({"error": "WQ BRAIN 认证失败"})

        _result = await asyncio.to_thread(
            _finalize_alpha_statuses, client, alpha_ids, None,
        )

        await asyncio.to_thread(client.close)

        return json.dumps(_result, ensure_ascii=False, indent=2, default=str)

    except Exception as e:
        _error_msg = str(e)
        logger.error(f"MCP wq_brain_finalize error: {e}")
        return json.dumps({"error": f"Finalize failed: {e}"})
    finally:
        await complete_mcp_task(task_id, _result, _error_msg)


@mcp.tool()
async def compute_factor_values(
    expression: str,
    universe: str = "csi500",
    start_date: str = "",
    end_date: str = "",
    universe_date: str | None = None,
    allow_remote_fetch: bool = False,
    submit_only: bool = False,
) -> str:
    """计算因子截面值，返回每个交易日所有股票的因子得分。

    Args:
        expression: 因子表达式，如 rank(ts_mean(close/open, 10))
        universe: 股票池，支持 small_scale / hs300 / csi500 / csi1000 / csi2000
        start_date: 起始日期 YYYY-MM-DD，默认 end_date 前 365 天
        end_date: 截止日期 YYYY-MM-DD，默认今天
        universe_date: 股票池成分股基准日期；默认使用 end_date
        allow_remote_fetch: 是否允许缓存缺失时阻塞式拉取远程行情；默认 False
        submit_only: 是否异步提交并立即返回 task_id；默认 False 保持同步行为

    Returns:
        JSON string with trading_days and data: [{date, values: {symbol: score}, count}].
    """
    task_params = {
        "universe": universe,
        "start_date": start_date,
        "end_date": end_date,
        "universe_date": universe_date,
        "allow_remote_fetch": allow_remote_fetch,
    }
    task_id = await start_mcp_task("compute_factor_values", expression, task_params)
    if submit_only:
        await update_mcp_task_progress(
            task_id,
            status="running",
            progress=0,
            progress_message="submitted compute_factor_values",
            stage="submitted",
        )
        _submit_mcp_background_task(_run_mcp_tool_with_existing_task(
            task_id,
            lambda: compute_factor_values(
                expression,
                universe=universe,
                start_date=start_date,
                end_date=end_date,
                universe_date=universe_date,
                allow_remote_fetch=allow_remote_fetch,
                submit_only=False,
            ),
        ))
        return _submitted_mcp_task_response(task_id)
    _error_msg = None
    _result = None
    try:
        await update_mcp_task_progress(
            task_id,
            status="validating",
            progress=2,
            progress_message="validating compute_factor_values request",
            stage="validating",
        )
        _mcp_cancel_check(task_id)
        req = _validate_factor_values_request(expression, universe, start_date, end_date)
        resolved_universe_date = _resolve_universe_date(universe_date, req.end_date)
        task_params["start_date"] = req.start_date
        task_params["end_date"] = req.end_date
        task_params["fetch_start"] = req.fetch_start
        task_params["universe_date"] = resolved_universe_date
        if allow_remote_fetch:
            _mcp_cancel_check(task_id)
            stock_codes = await asyncio.to_thread(
                get_universe,
                req.universe,
                resolved_universe_date,
                False,
            )
            _mcp_cancel_check(task_id)
            _raise_if_remote_prefetch_required(
                universe=req.universe,
                universe_date=resolved_universe_date,
                start_date=req.fetch_start,
                end_date=req.end_date,
                stock_codes=stock_codes,
            )
        await update_mcp_task_progress(
            task_id,
            status="fetching_data",
            progress=5,
            progress_message="fetching market data for factor values",
            stage="fetching_data",
        )
        _result = await asyncio.to_thread(
            _compute_factor_values_payload,
            expression,
            universe=universe,
            start_date=start_date,
            end_date=end_date,
            allow_remote_fetch=allow_remote_fetch,
            universe_date=resolved_universe_date,
            cancel_check=lambda: _mcp_cancel_check(task_id),
            progress_callback=_mcp_fetch_progress_callback(
                task_id,
                status="fetching_data",
                stage="fetching_data",
                base_progress=5,
                span=70,
            ),
        )
        _mcp_cancel_check(task_id)
        await update_mcp_task_progress(
            task_id,
            status="completed",
            progress=100,
            progress_message="compute_factor_values completed",
            stage="completed",
        )
        return json.dumps(_result, ensure_ascii=False, indent=2, default=str)
    except _RemotePrefetchRequired as exc:
        _result = exc.payload
        return json.dumps(_result, ensure_ascii=False, indent=2, default=str)
    except CancelledException:
        logger.info(f"compute_factor_values task {task_id} cancelled")
        _error_msg = "cancelled"
        _result = _mcp_cancelled_result(task_id)
        await update_mcp_task_progress(
            task_id,
            status="cancelled",
            progress_message="compute_factor_values cancelled",
            stage="cancelled",
        )
        return json.dumps(_result, ensure_ascii=False, indent=2)
    except Exception as e:
        _error_msg = str(e)
        _result = {"error": str(e)}
        logger.warning(f"compute_factor_values failed: {e}")
        return json.dumps(_result, ensure_ascii=False)
    finally:
        await complete_mcp_task(task_id, _result, _error_msg, expression)


# Operator documentation fallback
_OPERATORS_DOC = """
因子表达式操作符:

一元函数: rank, zscore, sign, log, abs, scale, tanh, sigmoid, exp, sqrt
时序函数: ts_mean, ts_std, ts_max, ts_min, ts_sum, ts_shift, ts_delta, ts_rank, ts_argmax, ts_argmin, decay_linear, product
  用法: ts_mean(close, 20) — 20日均值
双列时序: ts_corr(col1, col2, N), ts_cov(col1, col2, N)
二元函数: power, max, min
条件函数: clip(expr, lo, hi), where(cond, true_val, false_val)
算术运算: +, -, *, /, ^
比较运算: >, <, >=, <=, ==, !=
特殊变量: vwap, returns, adv{N} (如 adv20)
可用列名: open, high, low, close, volume, amount, pct_change
别名: delta=ts_delta, delay=ts_shift, correlation=ts_corr, covariance=ts_cov
"""
