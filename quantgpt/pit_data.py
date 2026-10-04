"""Provider-neutral effective intervals and point-in-time financial vintages."""

from __future__ import annotations

from typing import cast

import numpy as np
import pandas as pd


class DataCapabilityError(ValueError):
    """A required economic field cannot be supplied with equivalent meaning."""

    def __init__(self, fields: list[str], *, source: str, reason: str = "equivalent_field_unavailable"):
        self.fields = sorted(fields)
        self.source = source
        self.error_code = "data_capability_unavailable"
        self.reason = reason
        super().__init__(f"{self.error_code}: {source}: {', '.join(self.fields)} ({reason})")

    def to_dict(self) -> dict:
        return {"error_code": self.error_code, "fields": self.fields, "source": self.source,
                "reason": self.reason, "retryable": False, "next_action": "supply_equivalent_point_in_time_fields"}


def _utc(values: pd.Series, name: str) -> pd.Series:
    for value in values.dropna():
        if pd.Timestamp(value).tzinfo is None:
            raise ValueError(f"{name} requires explicit timezone")
    result = pd.to_datetime(values, utc=True, errors="raise")
    if result.isna().any():
        raise ValueError(f"{name} must not be missing")
    return result


def align_vintages(decisions: pd.DataFrame, facts: pd.DataFrame, *, fields: list[str]) -> pd.DataFrame:
    """Pick latest known period and revision independently for each economic field.

    Input facts are long form with units, stable security IDs and explicit UTC
    availability. Revisions of older periods never replace newer periods. Joining
    at a morning decision cannot expose an after-close announcement that day.
    """
    required = {"security_id", "field", "period_end", "available_at", "revision_id", "unit", "value"}
    missing = required - set(facts.columns)
    if missing:
        raise ValueError(f"vintage fields missing: {sorted(missing)}")
    output = decisions.copy()
    times = _utc(cast(pd.Series, output["decision_at"]), "decision_at")
    source = facts.copy()
    source["available_at"] = _utc(cast(pd.Series, source["available_at"]), "available_at")
    source["period_end"] = pd.to_datetime(source["period_end"], errors="raise")
    if source.duplicated(["security_id", "field", "period_end", "available_at"]).any():
        raise ValueError("ambiguous simultaneous financial vintages")
    if cast(pd.Series, source.groupby(["security_id", "field"])["unit"].nunique()).gt(1).any():
        raise ValueError("financial field unit changed without normalization")
    for field in fields:
        if field not in set(source["field"]):
            raise DataCapabilityError([field], source="vintage_store")
        values: list = []
        revisions: list = []
        availability: list = []
        for code, decision_at in zip(output["security_id"], times):
            eligible = cast(pd.DataFrame, source[(source["security_id"] == code) & (source["field"] == field)
                              & (source["available_at"] <= decision_at)])
            if eligible.empty:
                values.append(np.nan)
                revisions.append(None)
                availability.append(pd.NaT)
                continue
            row = eligible.sort_values(["period_end", "available_at"]).iloc[-1]
            values.append(row["value"])
            revisions.append(row["revision_id"])
            availability.append(row["available_at"])
        output[field] = values
        output[f"{field}_revision_id"] = revisions
        output[f"{field}_available_at"] = availability
    return output


def effective_members(intervals: pd.DataFrame, session: str, *, known_at: str | None = None) -> pd.DataFrame:
    """Resolve half-open [valid_from, valid_to) intervals without current-ticker inference."""
    required = {"security_id", "valid_from", "valid_to"}
    if required - set(intervals.columns):
        raise ValueError(f"interval fields missing: {sorted(required - set(intervals.columns))}")
    instant = pd.Timestamp(session)
    begin = pd.to_datetime(intervals["valid_from"])
    end = pd.to_datetime(intervals["valid_to"])
    mask = (begin <= instant) & (end.isna() | (instant < end))
    if known_at is not None:
        if "available_at" not in intervals:
            raise DataCapabilityError(["available_at"], source="membership_store")
        known = pd.Timestamp(known_at)
        if known.tzinfo is None:
            raise ValueError("known_at requires explicit timezone")
        mask &= _utc(cast(pd.Series, intervals["available_at"]), "available_at") <= known
    result = cast(pd.DataFrame, intervals[mask]).copy()
    if result["security_id"].duplicated().any():
        raise ValueError("overlapping security effective intervals")
    return result
