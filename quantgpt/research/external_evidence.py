"""Reconcile a deployment-approved reference release with a verified frozen panel.

This pure verifier accepts no URL or path and does not authenticate a caller's
license claims. Its caller must load the panel by content identity and the
reference release from the deployment's trusted, read-only catalog.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, cast

import exchange_calendars as xcals
import numpy as np
import pandas as pd

from quantgpt.research.contracts import EvaluationConfigV1
from quantgpt.research.market_inputs import check_dynamic_membership, check_signal_availability, price_basis

SECTIONS = ("prices", "historical_universe", "corporate_actions", "benchmark")
BLOCKERS = {"prices": "EXTERNAL_PRICE_COVERAGE_UNVERIFIED", "historical_universe": "HISTORICAL_UNIVERSE_UNVERIFIED",
            "corporate_actions": "CORPORATE_ACTION_COVERAGE_UNVERIFIED", "benchmark": "TOTAL_RETURN_BENCHMARK_UNVERIFIED"}
OHLCV = ("open", "high", "low", "close", "volume")
ACTION_TYPES = frozenset({"split", "cash_dividend", "delisting"})
PROVENANCE = ("provider", "dataset", "release", "source_ref", "license_ref")


class EvidenceIssue(ValueError):
    def __init__(self, status: str, code: str, **details: Any):
        super().__init__(code)
        self.status, self.code, self.details = status, code, details


def _missing(code: str, **details: Any) -> EvidenceIssue:
    return EvidenceIssue("insufficient_data", code, **details)


def _bad(code: str, **details: Any) -> EvidenceIssue:
    return EvidenceIssue("failed", code, **details)


def _required(record: Mapping, key: str) -> Any:
    value = record.get(key)
    if value is None or isinstance(value, str) and not value.strip():
        raise _missing("MISSING_EVIDENCE_FIELD", field=key)
    return value


def _rows(section: Mapping, key: str, *, allow_empty: bool = False) -> list[dict]:
    value = _required(section, key)
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise _bad("INVALID_EVIDENCE_RECORDS", field=key)
    if not value and not allow_empty:
        raise _missing("EMPTY_EVIDENCE_RECORDS", field=key)
    return value


def _day(value: Any) -> date:
    if isinstance(value, str):
        return date.fromisoformat(value)
    stamp = pd.Timestamp(value)
    if pd.isna(stamp):
        raise _bad("INVALID_SESSION_DATE")
    return cast(date, stamp.date())


def _instant(value: Any) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if pd.isna(timestamp) or timestamp.tzinfo is None:
        raise _bad("TIMEZONE_AWARE_KNOWN_AT_REQUIRED")
    return cast(pd.Timestamp, timestamp)


def _text(record: Mapping, key: str) -> str:
    value = _required(record, key)
    if not isinstance(value, str):
        raise _bad("INVALID_TEXT_FIELD", field=key)
    return value.strip()


def _number(record: Mapping, key: str, *, minimum: float = 0, positive: bool = False) -> float:
    value = _required(record, key)
    if isinstance(value, (bool, str)) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise _bad("NONFINITE_OR_INVALID_NUMBER", field=key)
    if value < minimum or positive and value == 0:
        raise _bad("VALUE_OUT_OF_RANGE", field=key)
    return float(value)


def _provenance(section: Mapping) -> dict[str, str]:
    source = _required(section, "provenance")
    if not isinstance(source, dict):
        raise _bad("INVALID_SOURCE_PROVENANCE")
    record = {key: _text(source, key) for key in PROVENANCE}
    if any(value.lower() in {"unknown", "unverified", "none", "n/a", "not_run", "not_available"}
           for value in record.values()):
        raise _missing("UNVERIFIED_SOURCE_OR_ENTITLEMENT")
    return record


@dataclass
class Scope:
    panel: pd.DataFrame
    days: list[date]
    decisions: dict[date, pd.Timestamp]
    scoring: list[date]
    start: date
    end: date


def _scope(frame: pd.DataFrame, config: EvaluationConfigV1, bundle: Mapping) -> Scope:
    manifest = frame.attrs.get("data_snapshot") or {}
    if (len(config.data_inputs) != 1 or not manifest.get("replayable")
            or config.data_inputs[0].version_status != "verified"):
        raise _missing("VERIFIED_REPLAYABLE_PANEL_REQUIRED")
    ref = config.data_inputs[0]
    if (_text(bundle, "manifest_id") != ref.manifest_id or ref.manifest_id != manifest.get("snapshot_id")
            or _text(bundle, "content_sha256") != ref.content_sha256
            or str(manifest.get("content_hash", "")).removeprefix("sha256:") != ref.content_sha256):
        raise _bad("EXTERNAL_SNAPSHOT_IDENTITY_MISMATCH")
    if _required(bundle, "scope") != config.scope.model_dump(mode="json"):
        raise _bad("EXTERNAL_SCOPE_MISMATCH")
    if config.scope.calendar_id not in xcals.get_calendar_names():
        raise _missing("INDEPENDENT_EXCHANGE_CALENDAR_REQUIRED")
    date_key = "session" if "session" in frame else "trade_date"
    if date_key not in frame or frame.empty:
        raise _missing("PANEL_SESSION_KEYS_REQUIRED")
    dates = pd.to_datetime(frame[date_key]).dt.date
    if dates.isna().any():
        raise _bad("INVALID_FROZEN_SESSION")
    end = config.window.end_session
    panel = frame.loc[dates <= end].copy()
    if panel.empty:
        raise _missing("EMPTY_FROZEN_INPUT_WINDOW")
    panel["_day"] = dates.loc[panel.index]
    start = min(panel["_day"].min(), config.window.start_session)
    if _day(_required(bundle, "coverage_start")) > start or _day(_required(bundle, "coverage_end")) < end:
        raise _missing("REFERENCE_PERIOD_INCOMPLETE", required_start=start.isoformat(), required_end=end.isoformat())
    calendar = xcals.get_calendar(config.scope.calendar_id, start=start - timedelta(days=14), end=end + timedelta(days=14))
    labels = calendar.sessions_in_range(start, end).tolist()
    decisions = {label.date(): calendar.session_close(label) for label in labels}
    days = list(decisions)
    scoring = [day for day in days if day >= config.window.start_session]
    if not scoring or len(days) - len(scoring) < config.window.warmup_observations:
        raise _missing("EXTERNAL_WINDOW_OR_WARMUP_INCOMPLETE")
    return Scope(panel, days, decisions, scoring, start, end)


def _keyed_panel(scope: Scope) -> pd.DataFrame:
    if "security_id" not in scope.panel:
        raise _missing("FROZEN_STABLE_SECURITY_ID_REQUIRED")
    if any(not isinstance(key, str) or not key.strip() for key in scope.panel.security_id):
        raise _bad("INVALID_FROZEN_SECURITY_ID")
    panel = scope.panel.set_index(["_day", "security_id"])
    if not panel.index.is_unique:
        raise _bad("DUPLICATE_FROZEN_SECURITY_SESSION")
    return panel


def _prices(s: Mapping, scope: Scope, config: EvaluationConfigV1) -> dict:
    source = s.get("provenance") or {}
    manifest = scope.panel.attrs["data_snapshot"]
    ref = config.data_inputs[0]
    if _text(source, "provider") != ref.source or manifest.get("vendor") != ref.source or _text(source, "dataset") != ref.feed:
        raise _bad("PRICE_SOURCE_MISMATCH")
    if _text(s, "currency") != config.scope.currency or _text(s, "adjustment") != "raw":
        raise _bad("PRICE_CURRENCY_OR_BASIS_MISMATCH")
    basis = price_basis(scope.panel)
    if basis == "unknown" or "currency" not in scope.panel:
        raise _missing("FROZEN_PRICE_BASIS_OR_CURRENCY_REQUIRED")
    if basis != "raw" or set(scope.panel.currency) != {config.scope.currency}:
        raise _bad("FROZEN_PRICE_BASIS_OR_CURRENCY_MISMATCH")
    if ref.adjustment not in {"raw", "none", "unadjusted"}:
        raise _bad("PRICE_REFERENCE_BASIS_MISMATCH")
    panel = _keyed_panel(scope)
    if set(OHLCV) - set(panel):
        raise _missing("FROZEN_OHLCV_REQUIRED")
    declared = scope.panel.attrs.get("calendar_sessions")
    if declared is None:
        raise _missing("FROZEN_CALENDAR_REQUIRED")
    declared_dates = [_day(pd.Timestamp(value)) for value in declared]
    if ([day for day in declared_dates if scope.start <= day <= scope.end] != scope.days
            or set(scope.panel["_day"]) != set(scope.days)):
        raise _bad("FROZEN_EXCHANGE_SESSION_GAP")
    reference = pd.DataFrame(_rows(s, "rows"))
    if {"session", "security_id", *OHLCV} - set(reference):
        raise _missing("REFERENCE_OHLCV_COLUMNS_REQUIRED")
    reference["_day"] = reference.session.map(_day)
    reference = reference.loc[reference["_day"].between(scope.start, scope.end)].set_index(["_day", "security_id"])
    if not reference.index.is_unique:
        raise _bad("DUPLICATE_REFERENCE_PRICE")
    missing, extra = reference.index.difference(panel.index), panel.index.difference(reference.index)
    if len(missing) or len(extra):
        raise _bad("PRICE_SECURITY_SESSION_COVERAGE_MISMATCH", panel_missing=len(missing), reference_missing=len(extra))
    expected = reference.reindex(panel.index)
    values = panel[list(OHLCV)].to_numpy(dtype=float)
    if (not np.isfinite(values).all() or (values[:, :4] <= 0).any() or (values[:, 4] < 0).any()
            or (values[:, 1] < np.max(values[:, [0, 2, 3]], axis=1)).any()
            or (values[:, 2] > np.min(values[:, [0, 1, 3]], axis=1)).any()):
        raise _bad("INVALID_FROZEN_RAW_OHLCV")
    matches = np.isclose(values, expected[list(OHLCV)].to_numpy(dtype=float), rtol=1e-10, atol=1e-12)
    for j, field in enumerate(OHLCV):
        if "raw_" + field in panel:
            matches[:, j] &= np.isclose(panel["raw_" + field].to_numpy(dtype=float), values[:, j], rtol=1e-10, atol=1e-12)
    if not matches.all():
        i, j = np.argwhere(~matches)[0]
        day, security = panel.index[int(i)]
        raise _bad("PRICE_OBSERVATION_MISMATCH", session=str(day), security_id=str(security), field=OHLCV[int(j)])
    counts = {"security_sessions": len(panel), "sessions": len(scope.days), "securities": int(scope.panel.security_id.nunique())}
    availability = check_signal_availability(scope.panel, OHLCV, calendar_id=config.scope.calendar_id,
                                            timezone=config.scope.timezone)
    if availability["status"] != "checked":
        raise _missing("PRICE_AVAILABILITY_UNVERIFIED", fields=availability["unverified_fields"], **counts)
    column = "close_available_at" if "close_available_at" in scope.panel else "available_at"
    close_times = pd.to_datetime(scope.panel[column], utc=True)
    if (close_times < scope.panel["_day"].map(scope.decisions.__getitem__)).any():
        raise _bad("OFFICIAL_CLOSE_PUBLISHED_BEFORE_EXCHANGE_CLOSE", **counts)
    return counts


def _members(s: Mapping, scope: Scope, config: EvaluationConfigV1) -> dict:
    panel = _keyed_panel(scope)
    if "ticker" not in panel:
        raise _missing("FROZEN_TICKER_HISTORY_REQUIRED")
    if config.scope.universe_mode == "dynamic_pit":
        if not scope.panel.attrs.get("universe_membership"):
            raise _missing("FROZEN_MEMBERSHIP_VINTAGE_REQUIRED")
        check_dynamic_membership(scope.panel, config.scope, scope.end)
    if config.scope.universe_mode == "fixed_cohort":
        if _instant(_required(s, "cohort_selected_at")) >= scope.decisions[scope.days[0]]:
            raise _bad("COHORT_SELECTED_AFTER_USE")
    members: dict[tuple[date, str], str] = {}
    ticker_keys: set[tuple[date, str]] = set()
    for item in _rows(s, "intervals"):
        key, ticker = _text(item, "security_id"), _text(item, "ticker")
        begin = _day(_required(item, "effective_from"))
        if "effective_to" not in item:
            raise _missing("MEMBERSHIP_INTERVAL_END_REQUIRED")
        end = _day(item["effective_to"]) if item["effective_to"] is not None else None
        known = _instant(_required(item, "known_at"))
        if end is not None and end <= begin:
            raise _bad("INVALID_MEMBERSHIP_INTERVAL")
        if end is not None and scope.start <= end <= scope.end:
            next_day = next((d for d in scope.days if d >= end), None)
            if next_day and _instant(_required(item, "end_known_at")) > scope.decisions[next_day]:
                raise _bad("MEMBERSHIP_EXIT_KNOWN_TOO_LATE", security_id=key)
        for day in scope.days:
            if day < begin or end is not None and day >= end:
                continue
            if known > scope.decisions[day]:
                raise _bad("MEMBERSHIP_KNOWN_TOO_LATE", session=str(day), security_id=key)
            if (day, key) in members or (day, ticker) in ticker_keys:
                raise _bad("AMBIGUOUS_SECURITY_OR_TICKER_HISTORY", session=str(day))
            members[day, key] = ticker
            ticker_keys.add((day, ticker))
    if members != panel["ticker"].to_dict():
        raise _bad("PIT_MEMBER_OR_TICKER_MISMATCH", reference_security_sessions=len(members), panel_security_sessions=len(panel))
    if config.scope.universe_mode == "fixed_cohort":
        original = {key for day, key in members if day == scope.days[0]}
        if any(key not in original for _, key in members):
            raise _bad("FIXED_COHORT_ADDED_SECURITY_AFTER_SELECTION")
    return {"security_sessions": len(members), "securities": len(set(key for _, key in members)),
            "universe_mode": config.scope.universe_mode}


def _event(item: Mapping, day: date) -> tuple:
    identity, security, kind = (_text(item, key) for key in ("event_id", "security_id", "type"))
    if kind not in ACTION_TYPES:
        raise _bad("UNSUPPORTED_CORPORATE_ACTION", event_id=identity)
    if kind == "split":
        return identity, security, kind, day, _number(item, "ratio", positive=True)
    amount = _number(item, "per_share" if kind == "cash_dividend" else "terminal_value")
    pay = _day(_required(item, "pay_session")) if kind == "cash_dividend" or item.get("pay_session") is not None else day
    if pay < day:
        raise _bad("CORPORATE_ACTION_PAY_BEFORE_ENTITLEMENT", event_id=identity)
    return identity, security, kind, day, amount, pay


def _actions(s: Mapping, scope: Scope, config: EvaluationConfigV1) -> dict:
    panel = _keyed_panel(scope)
    needed: dict[str, set[date]] = {}
    for day, security in panel.index:
        needed.setdefault(security, set()).add(day)
    if "corporate_actions" not in scope.panel.attrs:
        raise _missing("FROZEN_ACTION_LEDGER_REQUIRED")
    return _check_action_ledger(s, needed, scope.days, scope.start, scope.end, config.scope.currency,
                                scope.panel.attrs["corporate_actions"])


def _check_action_ledger(s: Mapping, needed: dict[str, set[date]], days: list[date], start_day: date,
                         end_day: date, currency: str, actual: list[dict]) -> dict:
    coverage: dict[tuple[str, str], set[date]] = {}
    for item in _rows(s, "coverage"):
        security = _text(item, "security_id")
        start, end = _day(_required(item, "start")), _day(_required(item, "end"))
        kinds = _required(item, "event_types")
        if start > end or not isinstance(kinds, list) or not set(kinds).issubset(ACTION_TYPES):
            raise _bad("INVALID_ACTION_COVERAGE")
        for kind in kinds:
            covered = coverage.setdefault((security, kind), set())
            covered.update(day for day in days if start <= day <= end)
    expected = _rows(s, "events", allow_empty=True)
    session_set = set(days)

    def selected(events: list[dict]) -> dict:
        entries = {}
        applied_splits: dict[tuple[str, date], set[str]] = {}
        for item in events:
            raw_date = item.get("ex_session") or _required(item, "session")
            day = _day(raw_date)
            if not start_day <= day <= end_day or _text(item, "security_id") not in needed:
                continue
            if item.get("session") is not None and _day(item["session"]) != day:
                raise _bad("CONFLICTING_ACTION_SESSION")
            if day not in session_set:
                raise _bad("CORPORATE_ACTION_OUTSIDE_EXCHANGE_SESSION")
            if item.get("currency", currency) != currency:
                raise _bad("CORPORATE_ACTION_CURRENCY_MISMATCH")
            event = _event(item, day)
            if event[0] in entries:
                raise _bad("DUPLICATE_CORPORATE_ACTION_ID", event_id=event[0])
            # The per-share amount is payable on the shares outstanding at
            # this point in the same security's ex-session event sequence.
            key = (event[1], day)
            if event[2] == "split":
                applied_splits.setdefault(key, set()).add(event[0])
            elif event[2] == "cash_dividend":
                event = (*event, frozenset(applied_splits.get(key, ())))
            entries[event[0]] = event
        return entries

    if not isinstance(actual, list) or any(not isinstance(e, dict) for e in actual):
        raise _bad("INVALID_FROZEN_CORPORATE_ACTIONS")
    reference, frozen = selected(expected), selected(actual)
    if reference != frozen:
        raise _bad("FROZEN_CORPORATE_ACTION_MISMATCH", reference_events=len(reference), frozen_events=len(frozen))
    terminal: dict[str, date] = {}
    for event in reference.values():
        if event[2] == "delisting":
            if event[1] in terminal:
                raise _bad("DUPLICATE_TERMINAL_SETTLEMENT", security_id=event[1])
            terminal[event[1]] = event[3]
    for security, dates in needed.items():
        # A disappearing price row is not proof that an investor's exposure
        # ended. Cover the interval through settlement or the report's cutoff.
        first, last = min(dates), terminal.get(security, end_day)
        dates_to_cover = {day for day in days if first <= day <= last}
        for kind in sorted(ACTION_TYPES):
            if not dates_to_cover.issubset(coverage.get((security, kind), set())):
                raise _missing("ACTION_COVERAGE_INCOMPLETE", security_id=security, event_type=kind,
                               matched_events=len(reference), securities=len(needed))
    for event in reference.values():
        if event[2] == "delisting" and any(d > event[3] for d in needed[event[1]]):
            raise _bad("SECURITY_TRADED_AFTER_DELISTING", security_id=event[1])
    return {"events": len(reference), "securities": len(needed), "covered_event_types": sorted(ACTION_TYPES)}


def import_action_release(panel: pd.DataFrame, release: dict) -> tuple[list[dict], dict]:
    """Validate an operator-supplied USD event ledger before freezing research input."""
    provenance = _provenance(release)
    events = _rows(release, "events", allow_empty=True)
    needed: dict[str, set[date]] = {}
    for security, session in zip(panel["security_id"], panel["session"], strict=True):
        needed.setdefault(security, set()).add(_day(session))
    days = sorted({_day(day) for day in panel.attrs["calendar_sessions"]})
    # A release may cover additional dates or instruments. Freeze only the
    # intersection, keeping the vendor's order for simultaneous entitlements.
    selected = []
    for event in events:
        day = _day(event.get("ex_session") or _required(event, "session"))
        if not days[0] <= day <= days[-1]:
            continue
        security = _text(event, "security_id")
        if security in needed:
            selected.append({**event, "event_id": _text(event, "event_id"), "type": _text(event, "type"),
                             "security_id": security, "ex_session": day.isoformat(), "session": day.isoformat()})
    _check_action_ledger(release, needed, days, days[0], days[-1], "USD", selected)
    return selected, provenance


def _benchmark(s: Mapping, scope: Scope, config: EvaluationConfigV1) -> dict:
    if config.scope.benchmark.strip().lower() in {"none", "unknown", "unverified", "n/a"}:
        raise _missing("FROZEN_BENCHMARK_ID_REQUIRED")
    if (_text(s, "benchmark_id") != config.scope.benchmark or _text(s, "currency") != config.scope.currency
            or _text(s, "return_type") != "total_return"):
        raise _bad("BENCHMARK_CONTRACT_MISMATCH")
    policy = _text(s, "reinvestment_policy")
    if policy.lower() in {"unknown", "none", "unverified", "not_reinvested", "price_only"}:
        raise _bad("BENCHMARK_REINVESTMENT_UNVERIFIED")
    levels = {}
    for row in _rows(s, "observations"):
        day = _day(_required(row, "session"))
        if not config.window.start_session <= day <= scope.end:
            continue
        if day in levels:
            raise _bad("DUPLICATE_BENCHMARK_SESSION", session=str(day))
        before, after = (_number(row, key, positive=True) for key in ("previous_total_return_level", "total_return_level"))
        r = _number(row, "return", minimum=-1)
        if not math.isclose(after / before - 1, r, rel_tol=1e-8, abs_tol=1e-10):
            raise _bad("BENCHMARK_RETURN_LEVEL_MISMATCH", session=str(day))
        levels[day] = (before, after)
    if not set(levels).issubset(scope.scoring):
        raise _bad("BENCHMARK_NONTRADING_SESSION")
    if set(scope.scoring) != set(levels):
        raise _missing("BENCHMARK_SESSION_COVERAGE_INCOMPLETE", missing_sessions=len(set(scope.scoring) - set(levels)))
    for prior, day in zip(scope.scoring, scope.scoring[1:], strict=False):
        if not math.isclose(levels[prior][1], levels[day][0], rel_tol=1e-10, abs_tol=1e-12):
            raise _bad("BENCHMARK_LEVEL_CHAIN_BROKEN", session=str(day))
    return {"sessions": len(levels), "benchmark_id": config.scope.benchmark, "currency": config.scope.currency,
            "reinvestment_policy": policy}


def _source_blockers(frame: pd.DataFrame, config: EvaluationConfigV1) -> list[str]:
    manifest = frame.attrs.get("data_snapshot") or {}
    meta = manifest.get("source_metadata") or {}
    places = [frame.attrs, meta, meta.get("input_attributes") or {}, manifest.get("query_params") or {}]
    for value in (frame.attrs.get("data_provenance"), meta.get("data_provenance")):
        places.extend(item for item in (value if isinstance(value, list) else [value]) if isinstance(item, dict))
    label = " ".join([str(manifest.get("vendor", "")), config.scope.market, config.scope.calendar_id]).lower()
    blockers = []
    if ("demo" in config.scope.market.lower() or any(token in label for token in ("synthetic", "fixture"))
            or any(item.get("synthetic") is True or item.get("status") == "synthetic" for item in places)):
        blockers.append("SYNTHETIC_SOURCE")
    if any(item.get("research_only") is True or item.get("real_market_validation") is False
           or item.get("status") == "research_only" for item in places):
        blockers.append("RESEARCH_ONLY_SOURCE")
    return blockers


def validate_external_evidence(frame: pd.DataFrame, config: EvaluationConfigV1, bundle: dict | None) -> dict:
    """Evaluate approved evidence without returning market levels or future performance."""
    checks: dict[str, dict] = {key: {"status": "not_run", "reason": "EVIDENCE_NOT_SUPPLIED"} for key in SECTIONS}
    report = {"schema_version": "external_market_acceptance/v1", "checks": checks,
              "manifest_id": config.data_inputs[0].manifest_id, "window": {
                  "start": config.window.start_session.isoformat(), "end": config.window.end_session.isoformat()}}
    if bundle is None:
        return {**report, "status": "not_run", "blockers": ["EXTERNAL_DATA_ACCEPTANCE_REQUIRED", *BLOCKERS.values()]}
    source_blockers = _source_blockers(frame, config)
    try:
        if not isinstance(bundle, dict) or _text(bundle, "schema_version") != "external_market_evidence/v1":
            raise _bad("EXTERNAL_EVIDENCE_SCHEMA_MISMATCH")
        scope = _scope(frame, config, bundle)
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        issue = exc if isinstance(exc, EvidenceIssue) else _bad("INVALID_EVIDENCE_BINDING")
        blockers = [issue.code, *source_blockers]
        return {**report, "status": issue.status, "blockers": blockers, "details": issue.details}
    for key, verify in zip(SECTIONS, (_prices, _members, _actions, _benchmark), strict=True):
        section = bundle.get(key)
        if section is None:
            continue
        if not isinstance(section, dict):
            checks[key] = {"status": "failed", "reason": "INVALID_EVIDENCE_SECTION"}
            continue
        supplied = section.get("provenance")
        source = {field: supplied[field] for field in PROVENANCE if isinstance(supplied, dict)
                  and isinstance(supplied.get(field), str)}
        provenance_issue = None
        try:
            _provenance(section)
        except EvidenceIssue as exc:
            provenance_issue = exc
        try:
            result = verify(section, scope, config)
            checks[key] = {"status": "passed", "provenance": source, **result}
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            issue = exc if isinstance(exc, EvidenceIssue) else _bad("INVALID_EVIDENCE_VALUES")
            checks[key] = {"status": issue.status, "reason": issue.code, "provenance": source, **issue.details}
        if provenance_issue:
            checks[key]["provenance_issue"] = {"reason": provenance_issue.code, **provenance_issue.details}
            if checks[key]["status"] == "passed":
                checks[key].update(status=provenance_issue.status, reason=provenance_issue.code)
    blockers = [BLOCKERS[key] for key, value in checks.items() if value["status"] != "passed"]
    blockers.extend(source_blockers)
    statuses = {value["status"] for value in checks.values()}
    status = "failed" if "SYNTHETIC_SOURCE" in blockers or "failed" in statuses else "insufficient_data" if blockers else "passed"
    return {**report, "status": status, "blockers": blockers}
