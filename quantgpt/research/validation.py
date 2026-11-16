"""Versioned evidence profiles; missing required evidence always blocks."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable

from sqlalchemy import case, func, select

from quantgpt.models import Experiment, ExperimentArtifact, ResearchDirection, ResearchRound
from quantgpt.research.contracts import content_hash
from quantgpt.research.evaluations import get_evaluation, read_artifact, verify_frozen_config
from quantgpt.research.evidence_catalog import read_published_evidence

POLICY_VERSION = "offline_validation/v4"


def archived_validation_notice(report: dict) -> dict | None:
    if report.get("policy") == POLICY_VERSION:
        return None
    return {"status": "historical_replay_required", "recorded_policy": report.get("policy"),
            "current_policy": POLICY_VERSION, "next_action": "validate_research_evaluation",
            "evaluation_id": report.get("evaluation_id")}


VALIDATION_PROFILES = {
    "local_factor": {"required": ("definition_config", "replayable_data", "data_quality", "oos",
                                  "label_boundaries", "walk_forward", "placebo", "trial_correction", "similarity"),
                     "optional": ("psr", "dsr"), "not_applicable": ("platform_checks",)},
    "local_strategy": {"required": ("strategy_identity", "factor_lineage", "replayable_data", "simulation",
                                    "strategy_oos", "label_boundaries", "walk_forward", "costs", "risk", "trial_correction"),
                       "optional": ("psr", "dsr"), "not_applicable": ("platform_checks",)},
    "wq_remote": {"required": ("remote_request", "remote_run_ref", "raw_response", "platform_checks", "platform_scope"),
                  "optional": ("psr", "dsr"), "not_applicable": ("local_holdout", "local_snapshot")},
}


def _portfolio_trial_keys(payloads: Iterable) -> set[str]:
    recorded: dict[str, set[str]] = {}
    declared_counts: dict[str, int] = {}
    for payload in payloads:
        if not isinstance(payload, dict) or not payload.get("portfolio_hash"):
            continue
        portfolio_hash = str(payload["portfolio_hash"])
        keys = recorded.setdefault(portfolio_hash, set())
        config = dict(payload.get("config") or {})
        config.pop("risk_aversion_candidates", None)
        config.pop("risk_aversion", None)
        # The final as-of allocation is not one of the observed validation trials.
        config.pop("asof_session", None)
        for idx, trial in enumerate(payload.get("parameter_trials") or []):
            if not isinstance(trial, dict) or trial.get("performance_observed") is not True:
                continue
            costs = [item["cost_bps"] for item in trial.get("cost_sensitivity", []) if "cost_bps" in item]
            for cost in set(costs or [config.get("transaction_cost_bps")]):
                if payload.get("signal_ref") and "risk_aversion" in trial:
                    identity = [payload["signal_ref"]["content_sha256"], payload["evaluation_id"],
                                config, trial["risk_aversion"], cost]
                else:
                    identity = [portfolio_hash, idx, cost]
                keys.add("portfolio:" + content_hash("quantgpt.portfolio_trial/v1", identity))
        declared = payload.get("performance_observed_trials")
        if isinstance(declared, int) and not isinstance(declared, bool):
            declared_counts[portfolio_hash] = max(declared, declared_counts.get(portfolio_hash, 0))
    # A retry may preserve only a count. Fill that gap once after combining
    # every persisted copy of the same optimization.
    combined: set[str] = set()
    for portfolio_hash, keys in recorded.items():
        combined.update(keys)
        combined.update(f"portfolio:{portfolio_hash}:legacy:{i}"
                        for i in range(len(keys), declared_counts.get(portfolio_hash, 0)))
    return combined


async def build_trial_manifest(session, project_id, evaluation_ids: Iterable[str] | None, *,
                               legacy_trials: Iterable[dict] = (), legacy_window_review: dict | None = None) -> dict:
    """Create a stable family of server-observed calculations for this project or a frozen round."""
    selection = select(Experiment.experiment_id, Experiment.evaluation_hash,
                       Experiment.result_summary["performance_observed"].as_boolean()).where(
        Experiment.project_id == project_id)
    optimization = select(ExperimentArtifact.payload).join(Experiment).where(
        Experiment.project_id == project_id, ExperimentArtifact.artifact_type == "portfolio_optimization")
    requested = set(evaluation_ids) if evaluation_ids is not None else None
    if requested is not None:
        selection = selection.where(Experiment.experiment_id.in_(requested))
        optimization = optimization.where(Experiment.experiment_id.in_(requested))
    rows = (await session.execute(selection)).all()
    if requested is not None and requested != {r[0] for r in rows}:
        raise ValueError("ROUND_EVALUATION_NOT_FOUND")
    evaluations = {f"evaluation:{digest or identity}" for identity, digest, observed in rows if observed}
    portfolios = _portfolio_trial_keys((await session.scalars(optimization)).all())
    imported = {record["trial_key"] for record in legacy_trials}
    keys = sorted(evaluations | portfolios | imported)
    manifest = {"schema_version": "trial_manifest/v1", "observed_trials": len(keys), "trial_keys": keys,
                "evaluation_ids": sorted(r[0] for r in rows if r[2]),
                "unobserved_evaluation_ids": sorted(r[0] for r in rows if not r[2]),
                "evaluation_trials": len(evaluations), "portfolio_trials": len(portfolios),
                "legacy_trials": len(imported - evaluations)}
    if legacy_window_review is not None:
        manifest["legacy_window_review"] = legacy_window_review
    return {**manifest, "manifest_hash": content_hash("quantgpt.trial_manifest/v1", manifest)}


async def observed_trial_count(session, project_id) -> int:
    identity = func.coalesce(Experiment.evaluation_hash, Experiment.experiment_id)
    evaluations = await session.scalar(select(func.count(func.distinct(identity))).where(
        Experiment.project_id == project_id, Experiment.result_summary["performance_observed"].as_boolean().is_(True)))
    payloads = await session.scalars(select(ExperimentArtifact.payload).join(Experiment).where(
        Experiment.project_id == project_id, ExperimentArtifact.artifact_type == "portfolio_optimization"))
    from quantgpt.research.legacy_history import round_legacy_trials

    directions = await session.scalars(select(ResearchDirection).join(ResearchRound).where(
        ResearchRound.project_id == project_id))
    legacy_keys = {trial["trial_key"] for trial in await round_legacy_trials(session, directions, project_id)}
    aliases = {key.removeprefix("evaluation:") for key in legacy_keys if key.startswith("evaluation:")}
    duplicated = 0
    if aliases:
        duplicated = await session.scalar(select(func.count(func.distinct(identity))).where(
            Experiment.project_id == project_id, Experiment.result_summary["performance_observed"].as_boolean().is_(True),
            identity.in_(aliases))) or 0
    return int(evaluations or 0) + len(_portfolio_trial_keys(payloads)) + len(legacy_keys) - duplicated


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
    offline = None
    external = None
    validated = False
    if profile != "wq_remote":
        round_context = None
        if row.evaluation_config is not None and row.evaluation_config["config"]["window"]["phase"] == "final":
            from quantgpt.research.round_final import final_validation_context

            round_context = await final_validation_context(session, project_id, evaluation_id)
        query = select(ExperimentArtifact).where(
            ExperimentArtifact.experiment_id == evaluation_id,
            ExperimentArtifact.artifact_type == "offline_validation",
            ExperimentArtifact.payload["profile"].as_string() == profile,
        )
        payload = ExperimentArtifact.payload
        query = query.where(payload["policy"].as_string() == POLICY_VERSION)
        if row.evaluation_config is not None:
            release = await asyncio.to_thread(read_published_evidence, row.evaluation_config["config"]["data_inputs"][0]["manifest_id"])
            query = query.where(payload["external_evidence_revision"].as_string() == release.revision)
        if round_context is not None:
            manifest = round_context["trial_manifest"]
            n_trials = manifest["observed_trials"]
            query = query.where(payload["round_id"].as_string() == str(round_context["round_id"]),
                                payload["selection_evaluation_id"].as_string() == round_context["selection_evaluation_id"],
                                payload["trial_manifest_hash"].as_string() == manifest["manifest_hash"])
        else:
            n_trials = await observed_trial_count(session, project_id)
        # An unpaired diagnostic or a late completion of an older replay cannot
        # supersede a current report with pre-exposure selection evidence.
        query = query.order_by(case((payload["observed_project_trials"].as_integer() == n_trials, 0), else_=1),
                               case((payload["selection_hash"].as_string().is_not(None), 0), else_=1),
                               ExperimentArtifact.created_at.asc(), ExperimentArtifact.id.asc()).limit(1)
        record = await session.scalar(query)
        if record is not None:
            artifact = await read_artifact(session, actor_id, project_id, record.id)
            report = artifact["payload"]
            if report.get("evaluation_hash") != row.evaluation_hash:
                raise ValueError("VALIDATION_EVIDENCE_IDENTITY_MISMATCH")
            offline = {"artifact_id": str(record.id), "content_sha256": record.content_hash,
                       "scope": report["scope"], "report_key": report["report_key"]}
            if round_context is not None:
                offline.update(round_id=report["round_id"], trial_manifest_hash=report["trial_manifest_hash"],
                               selection_evaluation_id=report["selection_evaluation_id"])
            if report["observed_project_trials"] != n_trials:
                blockers.append("OFFLINE_VALIDATION_STALE_TRIAL_COUNT")
            else:
                checks = report["checks"]
                validated = report["allowed"] is True
            external = report["external_acceptance"]
            blockers.extend(external["blockers"])
            if external["status"] != "passed" and "EXTERNAL_DATA_ACCEPTANCE_REQUIRED" not in blockers:
                blockers.append("EXTERNAL_DATA_ACCEPTANCE_REQUIRED")
            if external["status"] == "passed" and verify_frozen_config(row).engine.conformance != "verified":
                blockers.append("ENGINE_CONFORMANCE_UNVERIFIED")
        elif await session.scalar(select(ExperimentArtifact.id).where(
                ExperimentArtifact.experiment_id == evaluation_id, ExperimentArtifact.artifact_type == "offline_validation",
                payload["profile"].as_string() == profile).limit(1)):
            blockers.append("OFFLINE_VALIDATION_REPLAY_REQUIRED")
        if external is None:
            blockers.append("EXTERNAL_DATA_ACCEPTANCE_REQUIRED")
    for name in rules["required"]:
        check = checks.get(name)
        if not isinstance(check, dict) or check.get("status") != "passed":
            blockers.append(f"REQUIRED_EVIDENCE:{name}")
    if row.evidence_status in {None, "legacy_unverified", "research_only"} and not validated:
        blockers.append("EVIDENCE_NOT_VERIFIED")
    return {"schema_version": "validation_profile/v1", "profile": profile, "evaluation_id": evaluation_id,
            "evaluation_hash": row.evaluation_hash, "allowed": not blockers, "blockers": blockers,
            "checks": checks, "requirements": rules,
            "offline_validation": offline, "external_acceptance": external,
            "research_decision": "accepted" if not blockers else summary.get("research_decision", "insufficient_data")}
