"""Check the price basis and decision-time inputs of a frozen daily panel."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta
from typing import cast

import exchange_calendars
import pandas as pd

from quantgpt.fundamental_data import ALL_FUNDAMENTAL_NAMES
from quantgpt.pit_data import _utc, effective_members
from quantgpt.research.contracts import US_RESEARCH_FIELDS, FieldContract, ResearchScope

VINTAGE_FIELDS = {f.name for f in US_RESEARCH_FIELDS if f.requires_vintage} | ALL_FUNDAMENTAL_NAMES
RAW_PRICE_BASES = {"raw", "none", "unadjusted"}


def expected_sessions(calendar_id: str, start: date, end: date, declared: set[date], *, phase: str) -> list[date]:
    """Resolve scoring sessions with the same calendar for planning and preflight."""
    if calendar_id == "synthetic_weekdays":
        return [day.date() for day in pd.bdate_range(start, end).tolist()]
    if calendar_id in exchange_calendars.get_calendar_names():
        calendar = exchange_calendars.get_calendar(calendar_id, start=start - timedelta(days=14),
                                                   end=end + timedelta(days=14))
        return [day.date() for day in calendar.sessions_in_range(start, end).tolist()]
    if not declared or start < min(declared) or end > max(declared):
        raise ValueError(f"{phase}_WINDOW_OUTSIDE_PUBLISHED_CALENDAR")
    return sorted(day for day in declared if start <= day <= end)


def price_basis(frame: pd.DataFrame) -> str:
    """Reconcile vendor, manifest and row-level price declarations."""
    snapshot = frame.attrs.get("data_snapshot") or {}
    metadata = snapshot.get("source_metadata") or frame.attrs.get("source_metadata") or {}
    frozen = metadata.get("input_attributes") or {}
    query = snapshot.get("query_params") or {}

    def normalize(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        label = value.strip().lower()
        if not label or label == "unknown":
            return None
        return "raw" if label in RAW_PRICE_BASES else label

    declarations = {basis for value in (frame.attrs.get("adjustment"), metadata.get("adjustment"),
                    frozen.get("adjustment"), query.get("adjustment_type"), query.get("adjustment"))
                    if (basis := normalize(value))}
    complete = bool(declarations)
    for records in (frame.attrs.get("data_provenance"), frozen.get("data_provenance")):
        if isinstance(records, dict):
            records = [records]
        if not isinstance(records, (list, tuple)) or not records:
            continue
        bases = [normalize(record.get("adjustment")) if isinstance(record, dict) else None for record in records]
        complete |= all(bases)
        declarations.update(basis for basis in bases if basis)
    if "adjustment" in frame:
        bases = [normalize(value) for value in cast(pd.Series, frame["adjustment"]).unique()]
        complete |= bool(bases) and all(bases)
        declarations.update(basis for basis in bases if basis)
    if len(declarations) > 1:
        raise ValueError(f"CONFLICTING_PRICE_BASIS: {sorted(declarations)}")
    return next(iter(declarations)) if complete else "unknown"


def _decision_times(frame: pd.DataFrame, calendar_id: str, timezone: str) -> pd.Series:
    dates = pd.to_datetime(frame["session" if "session" in frame else "trade_date"]).dt.normalize()
    if dates.dt.tz is not None:
        dates = dates.dt.tz_localize(None)
    decisions = _utc(cast(pd.Series, frame["decision_at"]), "decision_at") if "decision_at" in frame else None
    if decisions is not None and not decisions.dt.tz_convert(timezone).dt.tz_localize(None).dt.normalize().eq(dates).all():
        raise ValueError("PIT_DECISION_SESSION_MISMATCH")
    if calendar_id == "synthetic_weekdays":
        closes = (dates + pd.Timedelta(hours=16)).dt.tz_localize(timezone).dt.tz_convert("UTC")
    elif calendar_id in exchange_calendars.get_calendar_names():
        days = pd.DatetimeIndex(dates.unique())
        start = cast(pd.Timestamp, days.min()).date() - timedelta(days=7)
        end = cast(pd.Timestamp, days.max()).date() + timedelta(days=7)
        calendar = exchange_calendars.get_calendar(calendar_id, start=start, end=end)
        if any(not calendar.is_session(day) for day in days):
            raise ValueError("PIT_SESSION_OUTSIDE_EXCHANGE_CALENDAR")
        closes = dates.map({day: calendar.session_close(day) for day in days})
    elif decisions is not None:
        return decisions
    else:
        raise ValueError("PIT_DECISION_TIME_REQUIRED: supply timezone-aware decision_at for this calendar")
    if decisions is not None and (decisions < closes).any():
        raise ValueError("PIT_DECISION_BEFORE_CLOSE")
    return closes if decisions is None else decisions


def check_signal_availability(frame: pd.DataFrame, fields: Iterable[str | FieldContract], *,
                              calendar_id: str, timezone: str) -> dict:
    """Validate the as-of join already present in the panel before its values can affect a signal."""
    field_list = tuple(fields)
    required = {item.name if isinstance(item, FieldContract) else item for item in field_list}
    vintage = (required & VINTAGE_FIELDS) | {item.name for item in field_list
                                            if isinstance(item, FieldContract) and item.requires_vintage}
    explicit = {name for name in required if f"{name}_available_at" in frame}
    unchecked = set(required)
    decisions = None

    def check(column: str, populated: pd.Series) -> None:
        nonlocal decisions
        if not populated.any():
            return
        if decisions is None:
            decisions = _decision_times(frame, calendar_id, timezone)
        available = _utc(cast(pd.Series, frame.loc[populated, column]), column)
        if (available > decisions.loc[populated]).any():
            raise ValueError(f"PIT_INPUT_AFTER_SIGNAL: {column}")

    if "available_at" in frame:
        check("available_at", pd.Series(True, index=frame.index))
        unchecked.clear()
    for field in sorted(vintage | explicit):
        time_column = f"{field}_available_at"
        if field not in frame or time_column not in frame:
            raise ValueError(f"PIT_FIELD_AVAILABILITY_REQUIRED: {field}")
        valid = cast(pd.Series, frame[field]).notna()
        if field in vintage:
            column = f"{field}_revision_id"
            if column not in frame or cast(pd.Series, frame.loc[valid, column]).replace("", None).isna().any():
                raise ValueError(f"PIT_FIELD_VINTAGE_REQUIRED: {field}")
        check(time_column, valid)
        unchecked.discard(field)
    return {"status": "unverified" if unchecked else "checked", "unverified_fields": sorted(unchecked),
            "vintage_fields": sorted(vintage), "external_acceptance": "not_run"}


def check_dynamic_membership(frame: pd.DataFrame, scope: ResearchScope, end_session: date) -> None:
    """Validate a prejoined dynamic panel against the memberships actually known on its decision dates."""
    if scope.universe_mode != "dynamic_pit":
        return
    records = frame.attrs.get("universe_membership")
    if not isinstance(records, list) or not records or "security_id" not in frame:
        raise ValueError("PIT_UNIVERSE_MEMBERSHIP_REQUIRED: supply frozen membership intervals and stable security_id "
                         "or declare a fixed_cohort")
    intervals = pd.DataFrame(records)
    if "security_id" not in intervals or cast(pd.Series, intervals["security_id"]).isna().any():
        raise ValueError("PIT_UNIVERSE_SECURITY_ID_REQUIRED")
    date_column = "session" if "session" in frame else "trade_date"
    dates = pd.to_datetime(frame[date_column])
    panel = cast(pd.DataFrame, frame.loc[dates.dt.date <= end_session]).reset_index(drop=True)
    if panel.empty:
        return
    decisions = _decision_times(panel, scope.calendar_id, scope.timezone)
    for day, group in panel.groupby(pd.to_datetime(panel[date_column]).dt.date):
        known_times = decisions.loc[group.index].unique()
        if len(known_times) != 1:
            raise ValueError("PIT_UNIVERSE_COMMON_DECISION_REQUIRED")
        eligible = effective_members(intervals, str(day), known_at=pd.Timestamp(known_times[0]).isoformat())
        if group["security_id"].duplicated().any() or set(group["security_id"]) != set(eligible["security_id"]):
            raise ValueError(f"PIT_UNIVERSE_MEMBERSHIP_MISMATCH: frozen panel on {day} "
                             "must contain exactly the members known at its decision time")
