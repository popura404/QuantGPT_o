"""Retain historical observations and check their relation to the final window."""

from __future__ import annotations

from datetime import date

from quantgpt.search_ledger import IncompleteIterationHistory, load_observed_iteration_trials
from quantgpt.strategy.adapters import canonical_market_name


def merge_trials(saved: list[dict], loaded: list[dict]) -> list[dict]:
    # Re-reading older tasks can supply missing dates or a deduplicated parent
    # identity. Retain the original evidence if its source is no longer available.
    merged = {(trial["task_id"], trial["attempt_id"]): trial for trial in saved}
    for trial in loaded:
        identity = (trial["task_id"], trial["attempt_id"])
        previous = merged.get(identity, {})
        old_scope, new_scope = previous.get("scope", {}), trial.get("scope", {})
        scope = {**old_scope, **new_scope}
        for field, bound in (("start_date", min), ("end_date", max)):
            dates = [part[field] for part in (old_scope, new_scope) if part.get(field)]
            if dates:
                scope[field] = bound(dates)
        merged[identity] = {**previous, **trial, "scope": scope}
    return [merged[key] for key in sorted(merged)]


async def round_legacy_trials(session, directions, project_id, *, unresolved_sources: set[str] | None = None) -> list[dict]:
    records = []
    for direction in directions:
        for source in direction.legacy_sources:
            saved = source["trials"]
            unresolved = source.get("history_blocker")
            # A v1 import lacked both scopes and parent evidence. Recheck the
            # original durable record, including retained successful attempts.
            if (unresolved or source.get("schema_version") != "legacy_iteration/v2"
                    or (source.get("holdout_review") or {}).get("status") == "unverified"):
                try:
                    current = await load_observed_iteration_trials(session, direction.actor_id, source["task_id"],
                                                                  project_id=project_id)
                    unresolved = None
                except IncompleteIterationHistory as exc:
                    current = exc.trials
                    unresolved = str(exc)
                except ValueError:
                    current = []
                    if source.get("schema_version") != "legacy_iteration/v2":
                        unresolved = "ITERATION_HISTORY_UNAVAILABLE"
                saved = merge_trials(saved, current)
            if unresolved and unresolved_sources is not None:
                unresolved_sources.add(source["task_id"])
            records.extend(saved)
    return records


def review_legacy_windows(trials: list[dict], config: dict, holdout: dict, *, unresolved_sources=()) -> dict:
    start = date.fromisoformat(holdout["start"])
    scope = config["scope"]
    market = canonical_market_name(scope["market"])
    manifests = {ref["manifest_id"] for ref in config["data_inputs"] if ref.get("manifest_id")}
    exposed, unknown = set(), set()
    for trial in trials:
        evidence = trial.get("scope") or {}
        key = trial["trial_key"]
        if trial.get("exposed_holdout") == holdout:
            exposed.add(key)
            continue
        try:
            last = date.fromisoformat(evidence["end_date"])
            first = date.fromisoformat(evidence["start_date"]) if evidence.get("start_date") else None
        except (KeyError, TypeError, ValueError):
            unknown.add(key)
            continue
        if first and first > last:
            unknown.add(key)
            continue
        if last < start:
            continue
        if first is None:
            unknown.add(key)
            continue
        historical_market = canonical_market_name(evidence.get("market", ""))
        same = (evidence.get("manifest_id") in manifests or historical_market == market
                or evidence.get("universe") == scope["universe_id"])
        if same:
            # A search conducted later in the same market also cannot be used
            # as a prospective choice for an earlier test window.
            exposed.add(key)
        elif {historical_market, market} != {"us", "a_share"}:
            # Dataset names and distinct snapshot IDs alone cannot establish
            # disjoint instruments. These two registered equity venues can.
            unknown.add(key)
    return {"status": "exposed" if exposed else "unverified" if unknown or unresolved_sources else "clear",
            "overlapping_or_later_trials": sorted(exposed), "unverified_trials": sorted(unknown),
            "incomplete_sources": sorted(unresolved_sources)}


def legacy_window_blocker(review: dict) -> str | None:
    if review["status"] == "exposed":
        return "LEGACY_HOLDOUT_EXPOSED"
    if review["status"] == "unverified":
        return "LEGACY_HOLDOUT_EVIDENCE_INCOMPLETE"
    return None
