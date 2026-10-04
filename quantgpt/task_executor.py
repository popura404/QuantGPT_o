"""Task execution backends: ProcessPool (default) or Celery (distributed).

Backtest orchestration threads call ``get_executor().submit_cpu_work(fn, ...)``
to offload CPU-bound pandas/numpy work to a separate process, bypassing the GIL.

Configuration via environment variables:
    QUANTGPT_TASK_BACKEND  = "process" | "celery" | "thread"  (default: process)
    QUANTGPT_WORKER_PROCESSES = int  (default: min(4, cpu_count))
    CELERY_BROKER_URL      = redis://...  (only for celery backend)
    CELERY_RESULT_BACKEND  = redis://...  (only for celery backend)
"""

from __future__ import annotations

import asyncio
import copy
import logging
import multiprocessing as mp
import os
import threading
import time
from abc import ABC, abstractmethod
from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor

from sqlalchemy import select

logger = logging.getLogger(__name__)

_dispatcher_task: asyncio.Task | None = None
_worker_monitors: set[asyncio.Task] = set()


def _dispatch_handler(payload: dict):
    """Only registered local requests may be replayed from the durable outbox."""
    if payload["task_type"] == "research_evaluation":
        from quantgpt.research.jobs import run_research_job
        return lambda: run_research_job(payload)
    if (payload.get("params") or {}).get("source") == "mcp":
        return None
    if payload["task_type"] == "strategy_backtest":
        from .routes.strategy import _run_strategy_backtest_task
        return lambda: _run_strategy_backtest_task(payload["task_id"], payload["params"], payload["user_id"],
                                                  attempt_id=payload.get("attempt_id"))
    if payload["task_type"] == "backtest":
        from .routes.backtest_tasks import AutoBacktestRequest, _run_backtest_task
        return lambda: _run_backtest_task(payload["task_id"], AutoBacktestRequest(**payload["params"]), payload["user_id"],
                                         attempt_id=payload.get("attempt_id"))
    return None


async def dispatch_durable_task(session, task_id: str) -> bool:
    """Claim a persisted local request before launching its worker."""
    from .models import Task
    from .task_store import _task_payload, claim_durable_task

    record = await session.get(Task, task_id)
    if record is None or _dispatch_handler(_task_payload(record)) is None:
        return False
    payload = await claim_durable_task(session, task_id)
    if payload is None:
        return False
    frozen_payload = copy.deepcopy(payload)
    handler = _dispatch_handler(copy.deepcopy(frozen_payload))
    worker = threading.Thread(target=handler, daemon=True, name=f"task-{task_id}")
    worker.start()
    monitor = asyncio.create_task(_monitor_worker(worker, frozen_payload))
    _worker_monitors.add(monitor)
    monitor.add_done_callback(_worker_monitors.discard)
    return True


async def _monitor_worker(worker: threading.Thread, payload: dict) -> None:
    from .task_store import (
        TASK_TIMEOUT_SECONDS,
        TERMINAL_TASK_STATUSES,
        persist_task_to_db_async,
        snapshot_task,
        transition_task,
    )

    deadline = time.monotonic() + TASK_TIMEOUT_SECONDS
    while worker.is_alive():
        await asyncio.sleep(10)
        task = snapshot_task(payload["task_id"], expected_attempt=payload.get("attempt_id"))
        if not task:
            return
        if task.get("status") in TERMINAL_TASK_STATUSES:
            return
        if time.monotonic() >= deadline:
            remote = ((payload.get("params") or {}).get("config", {}).get("backend") == "wq"
                      or bool(task.get("remote_run_ref")))
            task = transition_task(payload["task_id"], "remote_outcome_unknown" if remote else "failed", cancelled=True,
                                   expected_attempt=payload.get("attempt_id"),
                                   error="TASK_DEADLINE_EXCEEDED: execution exceeded the configured bound")
            if task is None:
                return
        await persist_task_to_db_async(payload["task_id"], payload["user_id"], task)


async def dispatch_pending_tasks_once(session=None) -> int:
    """Drain known local outbox records; unknown and remote requests remain visible."""
    from .db import _get_session_factory
    from .models import Task
    from .task_store import MAX_ACTIVE_TASKS, TERMINAL_TASK_STATUSES, recover_expired_tasks, tasks

    if session is None:
        async with _get_session_factory()() as owned:
            return await dispatch_pending_tasks_once(owned)
    await recover_expired_tasks(session)
    executing = sum(task.get("status") not in TERMINAL_TASK_STATUSES | {"queued", "pending"} for task in tasks.values())
    available = max(0, MAX_ACTIVE_TASKS - executing)
    if available == 0:
        return 0
    rows = (await session.scalars(select(Task).where(
        Task.dispatch_pending.is_(True), Task.status.in_(["queued", "pending"]),
        Task.task_type.in_(["backtest", "strategy_backtest", "research_evaluation"]),
    ).order_by(Task.created_at).limit(available))).all()
    count = 0
    for record in rows:
        if (record.params or {}).get("source") == "mcp" and str(record.task_type) != "research_evaluation":
            continue
        count += bool(await dispatch_durable_task(session, record.id))
    return count


def start_task_dispatcher() -> None:
    global _dispatcher_task
    if _dispatcher_task is not None and not _dispatcher_task.done():
        return

    async def loop():
        while True:
            try:
                await dispatch_pending_tasks_once()
            except Exception:
                logger.exception("Durable task dispatcher failed; queued records remain persisted")
            await asyncio.sleep(1)

    _dispatcher_task = asyncio.create_task(loop())


async def stop_task_dispatcher() -> None:
    global _dispatcher_task
    running = [task for task in [_dispatcher_task, *_worker_monitors] if task is not None]
    for task in running:
        task.cancel()
    await asyncio.gather(*running, return_exceptions=True)
    _worker_monitors.clear()
    _dispatcher_task = None


# ---------------------------------------------------------------------------
# Top-level wrappers — must be picklable for ProcessPoolExecutor
# ---------------------------------------------------------------------------

def _run_backtest_in_process(market_df, expression, n_groups, holding_period, **kwargs):
    from quantgpt.backtest import disable_api_context, enable_api_context, run_factor_backtest
    enable_api_context()
    try:
        return run_factor_backtest(market_df, expression, n_groups, holding_period, **kwargs)
    finally:
        disable_api_context()


def _run_backtest_precomputed_in_process(market_df, n_groups, holding_period, cost_rate, precomputed_factor):
    from quantgpt.backtest import disable_api_context, enable_api_context, run_factor_backtest
    enable_api_context()
    try:
        return run_factor_backtest(
            market_df, n_groups=n_groups, holding_period=holding_period,
            cost_rate=cost_rate, precomputed_factor=precomputed_factor,
        )
    finally:
        disable_api_context()


def _run_oos_backtest_in_process(market_df, expression, n_groups, holding_period, **kwargs):
    from quantgpt.backtest import disable_api_context, enable_api_context
    from quantgpt.validation.oos_backtest import run_factor_oos_backtest
    enable_api_context()
    try:
        return run_factor_oos_backtest(market_df, expression, n_groups, holding_period, **kwargs)
    finally:
        disable_api_context()


def _run_strategy_backtest_in_process(request_data, market_df=None, stock_codes=None):
    from quantgpt.strategy.backtest import StrategyBacktestRequest, run_strategy_backtest

    request = StrategyBacktestRequest.model_validate(request_data)
    return run_strategy_backtest(request, market_df=market_df, stock_codes=stock_codes)


# ---------------------------------------------------------------------------
# Abstract executor interface
# ---------------------------------------------------------------------------

class TaskExecutor(ABC):
    is_process_based: bool = False

    @abstractmethod
    def submit_cpu_work(self, fn, *args, **kwargs) -> Future:
        ...

    @abstractmethod
    def shutdown(self) -> None:
        ...


# ---------------------------------------------------------------------------
# ProcessPool executor (default)
# ---------------------------------------------------------------------------

class ProcessPoolTaskExecutor(TaskExecutor):
    is_process_based = True

    def __init__(self):
        cpu = os.cpu_count() or 4
        self._max_workers = int(os.environ.get("QUANTGPT_WORKER_PROCESSES", str(min(4, cpu))))
        ctx = mp.get_context("spawn")
        self._pool = ProcessPoolExecutor(max_workers=self._max_workers, mp_context=ctx)
        logger.info(f"ProcessPoolTaskExecutor initialized with {self._max_workers} workers")

    def submit_cpu_work(self, fn, *args, **kwargs) -> Future:
        return self._pool.submit(fn, *args, **kwargs)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False)
        logger.info("ProcessPoolTaskExecutor shut down")


# ---------------------------------------------------------------------------
# Thread executor (fallback / testing)
# ---------------------------------------------------------------------------

class ThreadTaskExecutor(TaskExecutor):
    is_process_based = False

    def __init__(self):
        self._pool = ThreadPoolExecutor(max_workers=int(os.environ.get("QUANTGPT_WORKER_PROCESSES", "4")))
        logger.info("ThreadTaskExecutor initialized")

    def submit_cpu_work(self, fn, *args, **kwargs) -> Future:
        return self._pool.submit(fn, *args, **kwargs)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False)


# ---------------------------------------------------------------------------
# Celery executor (distributed)
# ---------------------------------------------------------------------------

class CeleryTaskExecutor(TaskExecutor):
    is_process_based = True

    _FN_PATHS = {
        _run_backtest_in_process: "quantgpt.task_executor._run_backtest_in_process",
        _run_backtest_precomputed_in_process: "quantgpt.task_executor._run_backtest_precomputed_in_process",
        _run_oos_backtest_in_process: "quantgpt.task_executor._run_oos_backtest_in_process",
        _run_strategy_backtest_in_process: "quantgpt.task_executor._run_strategy_backtest_in_process",
    }

    def __init__(self):
        from .celery_app import CELERY_AVAILABLE, celery_app
        if not CELERY_AVAILABLE:
            raise RuntimeError("Celery backend requires installing the 'quantgpt[celery]' extra")
        self._app = celery_app
        logger.info("CeleryTaskExecutor initialized")

    def submit_cpu_work(self, fn, *args, **kwargs) -> Future:
        fn_path = self._FN_PATHS.get(fn)
        if fn_path is None:
            raise ValueError(f"Function not registered for Celery dispatch: {fn.__name__}")
        from .celery_app import run_cpu_work, to_json_transport
        ser_args = to_json_transport(list(args))
        ser_kwargs = to_json_transport(kwargs)
        async_result = run_cpu_work.apply_async(args=(fn_path, ser_args, ser_kwargs))
        return _CeleryFutureAdapter(async_result)

    def shutdown(self) -> None:
        pass


class _CeleryFutureAdapter(Future):
    """Adapt Celery AsyncResult to concurrent.futures.Future interface."""

    def __init__(self, async_result):
        super().__init__()
        self._ar = async_result

    def result(self, timeout=None):
        from .celery_app import from_json_transport
        raw = self._ar.get(timeout=timeout)
        return from_json_transport(raw)

    def cancel(self):
        self._ar.revoke(terminate=True)
        return True

    def done(self):
        return self._ar.ready()


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------

_executor: TaskExecutor | None = None


def get_executor() -> TaskExecutor:
    global _executor
    if _executor is not None:
        return _executor

    backend = os.environ.get("QUANTGPT_TASK_BACKEND", "process").lower()
    if backend == "celery":
        _executor = CeleryTaskExecutor()
    elif backend == "thread":
        _executor = ThreadTaskExecutor()
    else:
        _executor = ProcessPoolTaskExecutor()
    return _executor


def shutdown_executor() -> None:
    global _executor
    if _executor is not None:
        _executor.shutdown()
        _executor = None
