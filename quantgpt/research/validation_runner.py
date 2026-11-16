"""Server-owned offline validation; external market acceptance remains separate.

Reports are immutable artifacts. Replaying a saved evaluation never grants
permission to expose another final window or to accept caller-supplied checks.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any, cast

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from quantgpt.models import Experiment, ExperimentArtifact, FinalWindowExposure
from quantgpt.research.contracts import EvaluationConfigV1, FactorDefinitionV1, content_hash
from quantgpt.research.evaluations import (
    assert_publication_attempt,
    authorize_data_inputs,
    get_evaluation,
    load_evaluation_panel,
    read_artifact,
    verify_frozen_config,
    write_artifact,
)
from quantgpt.research.evidence_catalog import read_published_evidence
from quantgpt.research.external_evidence import validate_external_evidence
from quantgpt.research.market_inputs import check_signal_availability
from quantgpt.research.projects import require_project_member
from quantgpt.research.returns import annualized_return
from quantgpt.research.runtime import local_engine_identity
from quantgpt.research.validation import POLICY_VERSION, VALIDATION_PROFILES, observed_trial_count
from quantgpt.statistics.multiple_testing import block_sign_test

MIN_SESSIONS = 20


def _data_contract(config: EvaluationConfigV1, external: dict) -> dict:
    """Summarize the evidence checked for this exact scope and price history."""
    contract: dict = {"schema_version": "offline_data_acceptance/v2",
        "snapshot_integrity": "verified", "external_acceptance": external["status"],
        "inputs": [item.model_dump(mode="json") for item in config.data_inputs],
        "price_basis": "raw", "calendar": {"id": config.scope.calendar_id, "version": config.scope.calendar_version},
        "required_external_evidence": [
            {"kind": "prices", "status": "unverified",
             "required": ["licensed_source", "historical_coverage", "immutable_version", "raw_OHLCV", "missing_sessions"]},
            {"kind": "corporate_actions", "status": "unverified",
             "required": ["stable_event_id", "security_id", "ex_session", "split_ratio", "cash_amount",
                          "pay_session", "delisting_terminal_value", "coverage_attestation"]},
            {"kind": "historical_universe", "status": "unverified",
             "required": ["stable_security_id", "ticker_validity", "membership_intervals", "known_at", "delisted_securities"]},
            {"kind": "benchmark", "status": "unverified",
             "required": ["total_return", "currency", "dividend_reinvestment_policy", "complete_scoring_sessions"]},
        ]}
    for requirement in contract["required_external_evidence"]:
        requirement["status"] = external["checks"][requirement["kind"]]["status"]
    return contract


def _check(passed: bool, **details: Any) -> dict:
    return {"status": "passed" if passed else "failed", **details}


def _series(records: list) -> pd.Series:
    frame = pd.DataFrame(records)
    if frame.empty or not {"session", "return"}.issubset(frame):
        raise ValueError("RETURN_ARTIFACT_REQUIRED")
    dates = pd.DatetimeIndex(pd.to_datetime(frame["session"]))
    values = frame["return"].to_numpy(dtype=float)
    if dates.has_duplicates or dates.hasnans or not dates.is_monotonic_increasing or not np.isfinite(values).all():
        raise ValueError("RETURN_ARTIFACT_INVALID")
    if (values < -1).any():
        raise ValueError("RETURN_ARTIFACT_INVALID")
    return pd.Series(values, index=dates)


async def _read_returns(session: AsyncSession, actor: uuid.UUID, project: uuid.UUID, row: Experiment) -> tuple[pd.Series, dict]:
    references = [item for item in (row.result_summary or {}).get("artifacts", []) if item.get("kind") == "returns"]
    if len(references) != 1:
        raise ValueError("ONE_RETURN_ARTIFACT_REQUIRED")
    ref = references[0]
    artifact = await read_artifact(session, actor, project, uuid.UUID(ref["artifact_id"]))
    if (artifact["kind"] != "returns" or artifact["metadata"]["evaluation_hash"] != row.evaluation_hash
            or artifact["content_sha256"] != ref["content_sha256"]):
        raise ValueError("RETURN_ARTIFACT_LINEAGE_MISMATCH")
    return _series(artifact["payload"]), ref


def _candidate_identity(row: Experiment) -> dict:
    frozen = row.evaluation_config or {}
    config = verify_frozen_config(row).model_dump(mode="json")
    for key in ("start_session", "end_session", "phase"):
        config["window"].pop(key)
    return {"candidate": frozen.get("strategy", frozen.get("definition")), "config": config}


def _replay(frame: pd.DataFrame, frozen: dict, config: EvaluationConfigV1,
            start: str | None = None, end: str | None = None) -> dict:
    """Run the actual numerical entrypoint with the frozen direction and costs."""
    from quantgpt.backtest import api_context, run_factor_backtest

    start = start or config.window.start_session.isoformat()
    end = end or config.window.end_session.isoformat()
    panel = cast(pd.DataFrame, frame[pd.to_datetime(frame["trade_date"]) <= pd.Timestamp(end)].copy())
    simulation = config.simulation_config
    assert simulation is not None
    if "strategy" in frozen:
        from quantgpt.strategy.backtest import StrategyBacktestRequest, run_strategy_backtest
        from quantgpt.strategy.spec import StrategySpecV2

        spec = StrategySpecV2.model_validate(frozen["strategy"])
        panel.attrs["frozen_research_input"] = True
        request = StrategyBacktestRequest(spec=spec.calculation_spec(), start_date=start, end_date=end,
            benchmark=config.scope.benchmark, simulation_config=simulation,
            neutralize_industry="industry" in config.neutralization,
            neutralize_cap="market_cap" in config.neutralization,
            validation_stage="final" if config.window.phase == "final" else "selection")
        result = run_strategy_backtest(request, panel, final_authorized=True)
        return {"returns": result.strategy_returns, "weights": result.target_weights,
                "risk_logs": result.risk_logs, "costs": result.cost_by_rebalance,
                "simulation": result.diagnostics.get("ledger", {}), "signal_availability": result.diagnostics["signal_availability"]}
    definition = FactorDefinitionV1.model_validate(frozen["definition"])
    availability = check_signal_availability(panel, [*definition.fields, *config.neutralization],
        calendar_id=config.scope.calendar_id, timezone=config.scope.timezone)
    with api_context():
        output = run_factor_backtest(panel, expression=frozen["definition"]["expression"],
            holding_period=simulation.rebalance_every_sessions,
            cost_rate=(simulation.fees_bps + simulation.slippage_bps) / 10000,
            neutralize_industry="industry" in config.neutralization,
            neutralize_cap="market_cap" in config.neutralization,
            direction_mode="fixed", fixed_direction=1 if config.direction == "higher_is_better" else -1,
            rebalance_anchor=simulation.rebalance_anchor_session.isoformat(), simulation_config=simulation,
            evaluation_start=start, evaluation_end=end)
    return {"returns": output["strategy_returns"], "rank_ic": output["_raw_rank_ic_series"],
            "simulation": output["simulation_config"], "ledgers": output["_group_ledgers"], "signal_availability": availability}


def _numerical_checks(frame: pd.DataFrame, frozen: dict, config: EvaluationConfigV1,
                       saved: pd.Series, n_trials: int, cancel_check=None) -> dict:
    checks: dict[str, dict] = {}
    simulation = config.simulation_config
    assert simulation is not None
    if cancel_check:
        cancel_check()
    replay = _replay(frame, frozen, config)
    checks["signal_availability"] = replay["signal_availability"]
    actual = replay["returns"]
    same = actual.index.equals(saved.index) and np.allclose(actual.to_numpy(), saved.to_numpy(), atol=1e-9, rtol=1e-8)
    checks["simulation"] = _check(bool(same), observations=len(actual), method="frozen_entrypoint_replay")
    prices = frame[["open", "high", "low", "close", "volume"]].to_numpy(dtype=float)
    valid_prices = (np.isfinite(prices).all() and (prices[:, :4] > 0).all() and (prices[:, 4] >= 0).all()
                    and (prices[:, 1] >= prices[:, [0, 2, 3]].max(axis=1)).all()
                    and (prices[:, 2] <= prices[:, [0, 1, 3]].min(axis=1)).all())
    unique_rows = not frame.duplicated(["trade_date", "stock_code"]).any()
    checks["data_quality"] = _check(bool(valid_prices and unique_rows), scope="supplied_OHLCV_contract_only",
        historical_membership="unverified", corporate_action_coverage="unverified")
    calendar = pd.DatetimeIndex(pd.to_datetime(frame.attrs.get("calendar_sessions", frame.trade_date.unique()))).sort_values()
    scored = calendar[(calendar >= pd.Timestamp(config.window.start_session)) & (calendar <= pd.Timestamp(config.window.end_session))]
    checks["label_boundaries"] = _check(actual.index.equals(scored)
        and config.window.label_horizon_sessions == simulation.rebalance_every_sessions,
        expected_sessions=len(scored), computed_horizon=simulation.rebalance_every_sessions,
        observed_sessions=len(actual), horizon=config.window.label_horizon_sessions,
        purge_sessions=config.window.purge_sessions)
    if "rank_ic" in replay:
        ic = replay["rank_ic"]
        ends = calendar.get_indexer(ic.index) + config.window.label_horizon_sessions
        within = (ends < len(calendar)).all()
        if within and len(ends):
            within = bool((calendar.take(ends) <= pd.Timestamp(config.window.end_session)).all())
        checks["label_boundaries"]["status"] = "passed" if checks["label_boundaries"]["status"] == "passed" and within else "failed"
    checks["costs"] = _check(same, fees_bps=simulation.fees_bps,
        slippage_bps=simulation.slippage_bps, method="replay_with_frozen_both_sided_costs")
    if "weights" in replay:
        weights = replay["weights"]
        column = "target_weight" if "target_weight" in weights else "weight"
        invested_sessions = replay["simulation"].get("invested_sessions", 0)
        details = {"scope": "declared_weight_constraints_only", "events": replay["risk_logs"],
                   "invested_sessions": invested_sessions, "window_target_rows": len(weights)}
        if weights.empty or column not in weights or not invested_sessions:
            checks["risk"] = {"status": "insufficient_data", "reason": "NO_IN_WINDOW_POSITION_EVIDENCE", **details}
        else:
            exposure = weights.groupby("trade_date")[column].sum()
            limit = frozen["strategy"]["risk_rules"]["max_asset_weight"]
            valid = bool(pd.to_datetime(weights["trade_date"]).isin(scored).all()
                         and np.isfinite(weights[column]).all() and weights[column].between(0, limit + 1e-9).all()
                         and exposure.between(0, 1 + 1e-9).all())
            checks["risk"] = _check(valid, **details)
    # These are new simulations, with fresh cash in each forward window and a
    # fixed candidate; never describe slices of one return curve as retraining.
    if config.window.phase != "final" or len(scored) < 3 * MIN_SESSIONS:
        checks["walk_forward"] = {"status": "insufficient_data", "reason": "Requires a reserved final window of at least 60 sessions"}
    elif simulation.evaluation_start_state != "fresh_cash":
        checks["walk_forward"] = {"status": "insufficient_data", "reason": "Independent fold starting-state artifacts are required"}
    else:
        folds = []
        for positions in np.array_split(np.arange(len(scored)), 3):
            if cancel_check:
                cancel_check()
            first, last = cast(pd.Timestamp, scored[positions[0]]), cast(pd.Timestamp, scored[positions[-1]])
            result = _replay(frame, frozen, config, first.date().isoformat(), last.date().isoformat())
            returns = result["returns"]
            folds.append({"start": first.date().isoformat(), "end": last.date().isoformat(),
                          "sessions": len(returns), "total_return": float((1 + returns).prod() - 1),
                          "annual_return": annualized_return(returns)})
        checks["walk_forward"] = _check(sum(row["total_return"] > 0 for row in folds) >= 2,
            method="frozen_candidate_forward_replay", adaptive_retraining=False, windows=folds)
    # A serial-dependence-aware null diagnostic on saved net returns. It does not
    # establish benchmark-relative alpha or replace independent factor placebos.
    block = max(config.window.label_horizon_sessions, int(np.ceil(len(actual) ** (1 / 3))))
    if len(actual) < MIN_SESSIONS or n_trials < 1:
        checks["placebo"] = {"status": "insufficient_data", "reason": "At least 20 observations and registered trials required"}
        checks["trial_correction"] = dict(checks["placebo"])
    else:
        values = actual.to_numpy(dtype=float)
        blocks = [values[i:i + block] for i in range(0, len(values), block)]
        test = block_sign_test([float(chunk.sum()) for chunk in blocks], n_trials)
        checks["placebo"] = {"status": "diagnostic", "null_passed": test["p_value"] <= test["alpha"],
            "method": "block_sign_randomization_of_net_returns", **test, "block_length": block,
            "reason": "Independent cross-sectional factor placebo and benchmark evidence remain required"}
        checks["trial_correction"] = _check(test["adjusted_p_value"] <= test["alpha"], observed_project_trials=n_trials,
            raw_p_value=test["p_value"], **test, method="bonferroni")
        if not test["resolution_sufficient"]:
            checks["trial_correction"].update(status="insufficient_data", reason="RANDOMIZATION_RESOLUTION_LIMITED")
    return checks


async def run_local_validation(session: AsyncSession, actor_id: uuid.UUID, project_id: uuid.UUID,
                               evaluation_id: str, *, snapshot_root: str | Path,
                               selection_evaluation_id: str | None = None,
                               cancel_check=None, task_context: dict | None = None) -> dict:
    if cancel_check:
        cancel_check()
    await require_project_member(session, actor_id, project_id, write=True)
    row = await get_evaluation(session, actor_id, project_id, evaluation_id)
    config = verify_frozen_config(row)
    frozen = row.evaluation_config
    assert frozen is not None
    if config.backend != "local" or not (row.result_summary or {}).get("performance_observed"):
        raise ValueError("SUCCESSFUL_LOCAL_EVALUATION_REQUIRED")
    if config.engine != local_engine_identity():
        raise ValueError("CODE_VERSION_CHANGED: recompute the evaluation before validating it")
    await authorize_data_inputs(session, project_id, config)
    saved, returns_ref = await _read_returns(session, actor_id, project_id, row)
    round_context = None
    if config.window.phase == "final":
        from quantgpt.research.round_final import final_validation_context

        round_context = await final_validation_context(session, project_id, evaluation_id)
        if round_context is not None:
            canonical = round_context["selection_evaluation_id"]
            if selection_evaluation_id is not None and selection_evaluation_id != canonical:
                raise ValueError("ROUND_SELECTION_LINEAGE_MISMATCH")
            selection_evaluation_id = canonical
    selected = None
    source_refs = [returns_ref]
    if selection_evaluation_id:
        selected = await get_evaluation(session, actor_id, project_id, selection_evaluation_id)
        before = verify_frozen_config(selected)
        if (config.window.phase != "final" or before.window.phase != "selection"
                or before.window.end_session >= config.window.start_session
                or _candidate_identity(selected) != _candidate_identity(row)):
            raise ValueError("SELECTION_LINEAGE_MISMATCH")
        _, ref = await _read_returns(session, actor_id, project_id, selected)
        source_refs.append(ref)
    if config.window.phase == "final":
        exposure = await session.scalar(select(FinalWindowExposure.created_at).where(
            FinalWindowExposure.project_id == project_id, FinalWindowExposure.evaluation_id == evaluation_id,
            FinalWindowExposure.evaluation_hash == row.evaluation_hash))
        if exposure is None:
            raise ValueError("FINAL_RESERVATION_REQUIRED")
        if selected is not None:
            selected_before_exposure = await session.scalar(select(ExperimentArtifact.id).where(
                ExperimentArtifact.id == uuid.UUID(source_refs[-1]["artifact_id"]),
                ExperimentArtifact.created_at < exposure))
            if selected_before_exposure is None:
                raise ValueError("SELECTION_NOT_FROZEN_BEFORE_FINAL_EXPOSURE")
    trials = (round_context["trial_manifest"]["observed_trials"] if round_context is not None
              else await observed_trial_count(session, project_id))
    published = await asyncio.to_thread(read_published_evidence, config.data_inputs[0].manifest_id, include_payload=True)
    task_params = (task_context or {}).get("params") or {}
    if "external_evidence_revision" in task_params and task_params["external_evidence_revision"] != published.revision:
        raise ValueError("EXTERNAL_EVIDENCE_CHANGED: resubmit validation against the current release")
    identity = {"policy": POLICY_VERSION, "evaluation_hash": row.evaluation_hash,
                "external_evidence_revision": published.revision,
                "selection_hash": selected.evaluation_hash if selected else None, "observed_project_trials": trials,
                "source_artifacts": source_refs}
    if round_context is not None:
        identity.update(round_id=str(round_context["round_id"]), selection_evaluation_id=selection_evaluation_id,
                        trial_manifest_hash=round_context["trial_manifest"]["manifest_hash"])
    report_key = content_hash("quantgpt.offline_validation/v4", identity)
    existing = (await session.scalars(select(ExperimentArtifact.id).where(
        ExperimentArtifact.experiment_id == evaluation_id, ExperimentArtifact.artifact_type == "offline_validation",
        ExperimentArtifact.payload["report_key"].as_string() == report_key))).first()
    if existing:
        return (await read_artifact(session, actor_id, project_id, existing))["payload"]
    panel = await asyncio.to_thread(load_evaluation_panel, config, snapshot_root)
    external = await asyncio.to_thread(validate_external_evidence, panel, config, published.payload)
    if published.error:
        external.update(status="failed", blockers=["EXTERNAL_DATA_ACCEPTANCE_REQUIRED", published.error])
    external["evidence_revision"] = published.revision
    panel = cast(pd.DataFrame, panel[pd.to_datetime(panel.trade_date) <= pd.Timestamp(config.window.end_session)].copy())
    if "strategy" in frozen:
        from quantgpt.research.strategies import check_factor_lineage
        from quantgpt.strategy.spec import StrategySpecV2

        panel.attrs["factor_fields"] = await check_factor_lineage(session, actor_id, project_id,
            StrategySpecV2.model_validate(frozen["strategy"]), config)
    checks = await asyncio.to_thread(_numerical_checks, panel, frozen, config, saved, trials, cancel_check)
    checks["data_quality"].update(historical_membership=external["checks"]["historical_universe"]["status"],
                                  corporate_action_coverage=external["checks"]["corporate_actions"]["status"])
    checks["signal_availability"]["external_acceptance"] = external["status"]
    count_scope = "frozen_round" if round_context is not None else "project"
    checks["trial_correction"].update(trial_count_scope=count_scope, observed_trials=trials)
    profile = "local_strategy" if "strategy" in frozen else "local_factor"
    checks["strategy_identity" if profile == "local_strategy" else "definition_config"] = _check(True, evaluation_hash=row.evaluation_hash)
    checks["replayable_data"] = _check(True, snapshots=[ref.manifest_id for ref in config.data_inputs])
    checks["strategy_oos" if profile == "local_strategy" else "oos"] = _check(
        config.window.phase == "final" and selected is not None,
        selection_evaluation_id=selection_evaluation_id, phase=config.window.phase,
        reason="Requires the same frozen candidate selected before the reserved final window")
    if round_context is not None:
        from quantgpt.research.legacy_history import legacy_window_blocker

        review = round_context["trial_manifest"].get("legacy_window_review") or {"status": "clear"}
        blocker = legacy_window_blocker(review)
        if blocker:
            name = "strategy_oos" if profile == "local_strategy" else "oos"
            checks[name].update(status="failed" if review["status"] == "exposed" else "insufficient_data",
                                reason=blocker, legacy_window_review=review)
    if profile == "local_strategy":
        checks["factor_lineage"] = _check(True)
    else:
        checks["similarity"] = {"status": "insufficient_data", "reason": "A preregistered comparison library of aligned signals is required"}
    allowed = (config.window.phase == "final" and external["status"] == "passed" and not external["blockers"]
               and config.evidence_eligibility == "eligible_for_validation"
               and all(checks.get(name, {}).get("status") == "passed" for name in VALIDATION_PROFILES[profile]["required"]))
    report = {"schema_version": POLICY_VERSION, "report_key": report_key, **identity,
              "evaluation_id": evaluation_id, "profile": profile, "status": "completed",
              "trial_count_scope": count_scope,
              "checks": checks, "scope": "offline_replay_and_diagnostics", "allowed": allowed,
              "data_contract": _data_contract(config, external), "external_acceptance": external}
    for attempt in range(3):
        if cancel_check:
            cancel_check()
        try:
            await assert_publication_attempt(session, task_context)
            await require_project_member(session, actor_id, project_id, write=True)
            # A rollback expires ORM objects; reload before each publication.
            row = await get_evaluation(session, actor_id, project_id, evaluation_id)
            async with session.begin_nested():
                reference = await write_artifact(session, row, "offline_validation", report)
                saved_report = (await read_artifact(session, actor_id, project_id,
                                uuid.UUID(reference["artifact_id"])))["payload"]
                if saved_report != report:
                    raise ValueError("VALIDATION_REPORT_CONFLICT")
            await session.commit()
            return report
        except (IntegrityError, OperationalError) as exc:
            # SQLite readers racing to insert can receive BUSY_SNAPSHOT instead
            # of a uniqueness error. End that snapshot before bounded retry.
            sqlite_busy = (isinstance(exc, OperationalError)
                and (getattr(exc.orig, "sqlite_errorcode", 0) & 0xff) in {5, 6})
            await session.rollback()
            if attempt == 2 or (isinstance(exc, OperationalError) and not sqlite_busy):
                raise
            await asyncio.sleep(.01 * (attempt + 1))
    raise AssertionError("unreachable")
