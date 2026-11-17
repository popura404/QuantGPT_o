"""Preregister daily development and holdout windows using only frozen session keys."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterable
from datetime import date
from pathlib import Path

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from quantgpt.data_snapshots import load_frozen_market_frame
from quantgpt.research.contracts import DataInputRef, EvaluationConfigV1, ResearchScope, content_hash
from quantgpt.research.evaluations import authorize_snapshot_refs, check_factor_simulation_config
from quantgpt.research.market_inputs import expected_sessions
from quantgpt.research.projects import require_project_member
from quantgpt.research.runtime import local_engine_identity, snapshot_root

DEFAULT_SELECTION_SESSIONS = 756
DEFAULT_HOLDOUT_SESSIONS = 252
DEFAULT_WARMUP_SESSIONS = 30
DEFAULT_PURGE_SESSIONS = 5
WINDOW_POLICY = "daily_research_windows/v1"


def _count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("SESSION_COUNT_MUST_BE_A_POSITIVE_INTEGER")
    return value


def plan_session_windows(
    sessions: Iterable[date | str],
    *,
    selection_sessions: int = DEFAULT_SELECTION_SESSIONS,
    holdout_sessions: int = DEFAULT_HOLDOUT_SESSIONS,
    warmup_sessions: int = DEFAULT_WARMUP_SESSIONS,
    label_horizon_sessions: int = 5,
    purge_sessions: int = DEFAULT_PURGE_SESSIONS,
    as_of: str | None = None,
) -> dict:
    """Count disjoint blocks backwards from the last eligible published trading session."""
    for count in (selection_sessions, holdout_sessions, warmup_sessions, label_horizon_sessions, purge_sessions):
        _count(count)
    if min(selection_sessions, holdout_sessions) <= label_horizon_sessions:
        raise ValueError("SCORING_WINDOW_MUST_EXCEED_LABEL_HORIZON")
    gap = max(DEFAULT_PURGE_SESSIONS, purge_sessions, label_horizon_sessions)
    calendar = pd.DatetimeIndex(list(sessions))
    if calendar.hasnans:
        raise ValueError("RESEARCH_WINDOW_INVALID_CALENDAR")
    days = sorted(day.date() for day in calendar.tolist())
    if len(set(days)) != len(days):
        raise ValueError("RESEARCH_WINDOW_INVALID_CALENDAR")
    cutoff = date.fromisoformat(as_of) if as_of is not None else None
    if cutoff is not None:
        days = [day for day in days if day <= cutoff]
    required = warmup_sessions + selection_sessions + gap + holdout_sessions
    plan = {
        "policy": WINDOW_POLICY,
        "as_of": as_of,
        "warmup_sessions": warmup_sessions,
        "selection_sessions": selection_sessions,
        "purge_sessions": gap,
        "holdout_sessions": holdout_sessions,
        "label_horizon_sessions": label_horizon_sessions,
        "required_sessions": required,
        "available_sessions": len(days),
    }
    if len(days) < required:
        return {**plan, "status": "insufficient_data", "missing_sessions": required - len(days)}
    holdout = len(days) - holdout_sessions
    gap_start = holdout - gap
    selection = gap_start - selection_sessions
    return {
        **plan,
        "status": "ready",
        "missing_sessions": 0,
        "warmup_start": days[selection - warmup_sessions].isoformat(),
        "warmup_end": days[selection - 1].isoformat(),
        "selection_start": days[selection].isoformat(),
        "selection_end": days[gap_start - 1].isoformat(),
        "purge_start": days[gap_start].isoformat(),
        "purge_end": days[holdout - 1].isoformat(),
        "holdout_start": days[holdout].isoformat(),
        "holdout_end": days[-1].isoformat(),
    }


def plan_snapshot_windows(
    ref: DataInputRef, scope: ResearchScope, root: Path, *, as_of: str | None = None, **counts: int
) -> dict:
    """Check a previously granted frozen panel, including warmup and purge coverage."""
    if not ref.manifest_id:
        raise ValueError("DATA_SNAPSHOT_REQUIRED")
    try:
        frame = load_frozen_market_frame(ref.manifest_id, root)
    except OSError as exc:
        raise ValueError("REGISTERED_SNAPSHOT_UNAVAILABLE") from exc
    manifest = frame.attrs["data_snapshot"]
    if ref.content_sha256 != manifest["content_hash"].removeprefix("sha256:"):
        raise ValueError("DATA_SNAPSHOT_CONTENT_MISMATCH")
    published_calendar = frame.attrs.get("calendar_sessions")
    if published_calendar is None:
        raise ValueError("RESEARCH_WINDOW_INVALID_CALENDAR")
    declared = pd.DatetimeIndex(published_calendar)
    date_column = "session" if "session" in frame else "trade_date"
    if date_column not in frame or declared.empty or declared.hasnans or declared.has_duplicates:
        raise ValueError("RESEARCH_WINDOW_INVALID_CALENDAR")
    observed_index = pd.DatetimeIndex(frame[date_column])
    if observed_index.empty or observed_index.hasnans:
        raise ValueError("RESEARCH_WINDOW_INVALID_CALENDAR")
    observed = {day.date() for day in observed_index.unique().tolist()}
    published = {day.date() for day in declared.tolist()}
    if len(published) != len(declared):
        raise ValueError("RESEARCH_WINDOW_INVALID_CALENDAR")
    # A missing first or last price must not shift the boundaries of a published dataset.
    query = manifest.get("query_params") or {}
    first = date.fromisoformat(str(query["from"])) if query.get("from") else min(published)
    last = date.fromisoformat(str(query["to"])) if query.get("to") else max(published)
    if first > last:
        raise ValueError("RESEARCH_WINDOW_INVALID_COVERAGE_RANGE")
    if as_of is not None:
        last = min(last, date.fromisoformat(as_of))
    days = expected_sessions(scope.calendar_id, first, last, published, phase="PLANNED") if first <= last else []
    plan = plan_session_windows(days, as_of=as_of, **counts)
    needed = days[-plan["required_sessions"] :]
    gaps = [day.isoformat() for day in needed if day not in observed or day not in published]
    if gaps:
        # Never move the split or compress the purge across missing trading sessions.
        plan.update(
            status="insufficient_data",
            reason="SNAPSHOT_SESSION_GAP",
            missing_sessions=plan["missing_sessions"] + len(gaps),
            missing_calendar_sessions=gaps,
        )
    if needed:
        extra = (observed | published) - set(days)
        if any(needed[0] <= day <= needed[-1] for day in extra):
            raise ValueError("RESEARCH_WINDOW_OFF_CALENDAR_SESSION")
    plan["available_sessions"] = len(set(days) & published & observed)
    return plan


async def plan_project_windows(
    session: AsyncSession, actor_id: uuid.UUID, project_id: uuid.UUID, *, config: dict, as_of: str | None = None
) -> dict:
    """Prepare create_round arguments without creating a holdout, trial, or task."""
    await require_project_member(session, actor_id, project_id)
    window = config.get("window", {})
    if not isinstance(window, dict) or "start_session" in window or "end_session" in window:
        raise ValueError("WINDOW_PLAN_REQUIRES_UNDATED_SELECTION_TEMPLATE")
    if config.get("backend") != "local" or window.get("phase", "selection") != "selection":
        raise ValueError("LOCAL_SELECTION_CONFIG_REQUIRED")
    scope = ResearchScope.model_validate(config.get("scope"))
    simulation = config.get("simulation_config")
    if not isinstance(simulation, dict):
        raise ValueError("LOCAL_SIMULATION_CONFIG_REQUIRED")
    holding = _count(simulation.get("rebalance_every_sessions", 1))
    horizon = _count(window.get("label_horizon_sessions", holding))
    if horizon != holding:
        raise ValueError("LABEL_HORIZON_UNSUPPORTED: label horizon must match the current holding period")
    inputs = config.get("data_inputs")
    if not isinstance(inputs, (list, tuple)) or len(inputs) != 1:
        raise ValueError("A single complete joined manifest is required")
    ref = DataInputRef.model_validate(inputs[0])
    await authorize_snapshot_refs(session, project_id, (ref,))
    plan = await asyncio.to_thread(
        plan_snapshot_windows,
        ref,
        scope,
        snapshot_root(),
        as_of=as_of,
        warmup_sessions=max(
            DEFAULT_WARMUP_SESSIONS, _count(window.get("warmup_observations", DEFAULT_WARMUP_SESSIONS))
        ),
        label_horizon_sessions=horizon,
        purge_sessions=_count(window.get("purge_sessions", DEFAULT_PURGE_SESSIONS)),
    )
    if plan["status"] != "ready":
        return {**plan, "round_setup": None}
    split = {
        key: plan[key]
        for key in (
            "warmup_start",
            "selection_start",
            "selection_end",
            "holdout_start",
            "holdout_end",
            "warmup_sessions",
            "purge_sessions",
            "label_horizon_sessions",
        )
    }
    dated = {
        **window,
        "phase": "selection",
        "start_session": plan["selection_start"],
        "end_session": plan["selection_end"],
        "warmup_observations": plan["warmup_sessions"],
        "label_horizon_sessions": horizon,
        "purge_sessions": plan["purge_sessions"],
        "split_id": WINDOW_POLICY,
        "split_hash": content_hash(WINDOW_POLICY, [ref.manifest_id, scope.model_dump(mode="json"), split]),
    }
    effective = EvaluationConfigV1.model_validate(
        {
            **config,
            "window": dated,
            "engine": local_engine_identity(),
            "simulation_config": {"rebalance_anchor_session": plan["warmup_start"], **simulation},
        }
    )
    check_factor_simulation_config(effective)
    return {
        **plan,
        "round_setup": {
            "config": effective.model_dump(mode="json"),
            "holdout_start": plan["holdout_start"],
            "holdout_end": plan["holdout_end"],
        },
    }
