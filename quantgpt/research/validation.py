"""Versioned evidence profiles; missing required evidence always blocks."""

from __future__ import annotations

from sqlalchemy import func, select

from quantgpt.models import Experiment, ExperimentArtifact
from quantgpt.research.evaluations import get_evaluation, verify_frozen_config

VALIDATION_PROFILES = {
    "local_factor": {"required": ("definition_config", "replayable_data", "data_quality", "oos",
                                  "label_boundaries", "walk_forward", "placebo", "trial_correction", "similarity"),
                     "optional": ("psr", "dsr"), "not_applicable": ("platform_checks",)},
    "local_strategy": {"required": ("strategy_identity", "factor_lineage", "replayable_data", "simulation",
                                    "strategy_oos", "walk_forward", "costs", "risk", "trial_correction"),
                       "optional": ("psr", "dsr"), "not_applicable": ("platform_checks",)},
    "wq_remote": {"required": ("remote_request", "remote_run_ref", "raw_response", "platform_checks", "platform_scope"),
                  "optional": ("psr", "dsr"), "not_applicable": ("local_holdout", "local_snapshot")},
}


async def observed_trial_count(session, project_id) -> int:
    # JSON predicate is portable across SQLite/PG and counts observed rejected
    # candidates too. Same immutable retries share one evaluation identity.
    evaluations = int((await session.scalar(select(func.count()).select_from(Experiment).where(
        Experiment.project_id == project_id,
        Experiment.result_summary["performance_observed"].as_boolean().is_(True),
    ))) or 0)
    payloads = (await session.scalars(select(ExperimentArtifact.payload).join(Experiment).where(
        Experiment.project_id == project_id, ExperimentArtifact.artifact_type == "portfolio_optimization",
    ))).all()
    portfolios: dict[str, int] = {}
    for payload in payloads:
        if not isinstance(payload, dict) or not payload.get("portfolio_hash"):
            continue
        trials = payload.get("parameter_trials") or []
        observed = sum(isinstance(trial, dict) and trial.get("performance_observed") is True for trial in trials)
        declared = payload.get("performance_observed_trials")
        if isinstance(declared, int) and not isinstance(declared, bool):
            observed = max(observed, declared)
        identity = str(payload["portfolio_hash"])
        portfolios[identity] = max(portfolios.get(identity, 0), observed)
    return evaluations + sum(portfolios.values())


async def evaluate_profile(session, actor_id, project_id, evaluation_id: str, *, profile: str) -> dict:
    if profile not in VALIDATION_PROFILES:
        raise ValueError("Unknown validation profile")
    row = await get_evaluation(session, actor_id, project_id, evaluation_id)
    rules = VALIDATION_PROFILES[profile]
    blockers = []
    if row.evaluation_config is None:
        blockers.append("LEGACY_RECOMPUTE_REQUIRED")
    else:
        config = verify_frozen_config(row)
        if (profile == "wq_remote") != (config.backend == "wq"):
            blockers.append("EVIDENCE_SCOPE_MISMATCH")
        if profile != "wq_remote" and config.window.phase != "final":
            blockers.append("FINAL_TEST_REQUIRED")
    summary = row.result_summary or {}
    checks = summary.get("validation_checks") or {}
    for name in rules["required"]:
        check = checks.get(name)
        if not isinstance(check, dict) or check.get("status") != "passed":
            blockers.append(f"REQUIRED_EVIDENCE:{name}")
    if row.evidence_status in {None, "legacy_unverified", "research_only"}:
        blockers.append("EVIDENCE_NOT_VERIFIED")
    return {"schema_version": "validation_profile/v1", "profile": profile, "evaluation_id": evaluation_id,
            "evaluation_hash": row.evaluation_hash, "allowed": not blockers, "blockers": blockers,
            "checks": checks, "requirements": rules,
            "research_decision": "accepted" if not blockers else summary.get("research_decision", "insufficient_data")}
