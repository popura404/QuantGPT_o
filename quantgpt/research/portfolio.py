"""Long-only research allocation with past-only estimates and explicit feasibility.

No optimizer output is export authorization. The saved signal, market snapshot,
estimation cutoff and constraints are all included in the result identity.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import date
from pathlib import Path
from typing import Any, cast

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator
from scipy.optimize import linprog, minimize
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from quantgpt.models import ExperimentArtifact
from quantgpt.research.contracts import content_hash
from quantgpt.research.evaluations import (
    get_evaluation,
    load_evaluation_panel,
    read_artifact,
    verify_frozen_config,
    write_artifact,
)
from quantgpt.research.projects import require_project_member


class PortfolioOptimizationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    asof_session: date
    lookback_sessions: int = Field(default=60, ge=3, le=2520)
    min_observations: int = Field(default=20, ge=3)
    max_asset_weight: float = Field(default=.1, gt=0, le=1)
    cash_min: float = Field(default=0, ge=0, le=1)
    cash_max: float = Field(default=1, ge=0, le=1)
    max_turnover: float | None = Field(default=None, ge=0, le=2)
    industry_max_weights: dict[str, float] = Field(default_factory=dict)
    beta_min: float | None = None
    beta_max: float | None = None
    log_market_cap_min: float | None = None
    log_market_cap_max: float | None = None
    max_adv_participation: float | None = Field(default=None, gt=0, le=1)
    portfolio_nav: float = Field(default=100_000, gt=0)
    previous_weights: dict[str, float] = Field(default_factory=dict)
    risk_aversion: float = Field(default=10, ge=0)
    covariance_shrinkage: float = Field(default=.2, ge=0, le=1)
    transaction_cost_bps: float = Field(default=5, ge=0, le=1000)
    training_end: date | None = None
    validation_end: date | None = None
    risk_aversion_candidates: tuple[float, ...] = Field(default=(), max_length=12)

    @model_validator(mode="after")
    def validate_limits(self):
        if self.cash_min > self.cash_max:
            raise ValueError("cash_min exceeds cash_max")
        if self.min_observations > self.lookback_sessions - 1:
            raise ValueError("lookback_sessions must include enough returns")
        if self.risk_aversion_candidates:
            if (self.training_end is None or self.validation_end is None
                    or not self.training_end < self.validation_end < self.asof_session):
                raise ValueError("Parameter selection requires training_end < validation_end < asof_session")
            if any(not np.isfinite(value) or value < 0 for value in self.risk_aversion_candidates):
                raise ValueError("Risk aversion candidates must be finite and nonnegative")
        if any(not np.isfinite(value) or value < 0 for value in self.previous_weights.values()):
            raise ValueError("Previous weights must be finite and nonnegative")
        if sum(self.previous_weights.values()) > 1 + 1e-10:
            raise ValueError("Leverage is not supported")
        if any(not np.isfinite(value) or not 0 <= value <= 1 for value in self.industry_max_weights.values()):
            raise ValueError("Industry caps must be within [0, 1]")
        for lower, upper in ((self.beta_min, self.beta_max), (self.log_market_cap_min, self.log_market_cap_max)):
            if any(value is not None and not np.isfinite(value) for value in (lower, upper)):
                raise ValueError("Exposure bounds must be finite")
            if lower is not None and upper is not None and lower > upper:
                raise ValueError("Exposure minimum exceeds maximum")
        return self


def _panel(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    if "security_id" not in frame:
        frame["security_id"] = frame["stock_code"]
    if "session" not in frame:
        frame["session"] = frame["trade_date"]
    frame["security_id"] = frame["security_id"].astype(str)
    frame["session"] = pd.to_datetime(frame["session"]).dt.normalize()
    if frame.duplicated(["security_id", "session"]).any():
        raise ValueError("DUPLICATE_SECURITY_SESSION")
    return frame


def _blocked(code: str, detail: str) -> dict:
    return {"status": "blocked", "feasible": False, "error_code": code, "detail": detail,
            "target_weights": [], "fallback_used": False}


def optimize_portfolio(signals: pd.DataFrame, market_frame: pd.DataFrame,
                        config: PortfolioOptimizationConfig) -> dict:
    """Pure solver. Eligibility defines candidates; forecasts are past return means.

    Turnover and costs both use the sum of absolute buy and sell notional / NAV.
    Exposures are total NAV exposures, so cash contributes zero.
    """
    if config.risk_aversion_candidates:
        return _select_parameters(signals, market_frame, config)
    cutoff = pd.Timestamp(config.asof_session)
    signals = signals.copy()
    if "security_id" not in signals:
        signals["security_id"] = signals["stock_code"]
    signal_dates = pd.to_datetime(signals["session" if "session" in signals else "trade_date"]).dt.normalize()
    signals = cast(pd.DataFrame, signals[signal_dates == cutoff]).copy()
    if "eligibility" in signals:
        signals = cast(pd.DataFrame, signals[signals["eligibility"].eq(True)])
    assets = sorted(set(signals["security_id"].astype(str)) | set(config.previous_weights))
    if not assets:
        return _blocked("NO_ELIGIBLE_SIGNALS", "No candidate or existing holding at the requested session")
    frame = _panel(market_frame)
    frame = cast(pd.DataFrame, frame[(frame["session"] <= cutoff) & frame["security_id"].isin(assets)])
    if "close" not in frame:
        return _blocked("RISK_DATA_UNAVAILABLE", "Point-in-time factor close is required")
    prices = frame.pivot(index="session", columns="security_id", values="close").reindex(columns=assets).tail(
        config.lookback_sessions)
    if prices.empty or prices.index[-1] != cutoff:
        return _blocked("RISK_DATA_UNAVAILABLE", "The cutoff session has no complete observed prices")
    if (prices <= 0).to_numpy().any():
        return _blocked("RISK_DATA_UNAVAILABLE", "Observed prices must be positive")
    returns = prices.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan).dropna()
    if len(returns) < config.min_observations:
        return _blocked("RISK_DATA_UNAVAILABLE", "Insufficient common past return observations; gaps are not filled")
    current = cast(pd.DataFrame, frame[frame["session"] == cutoff]).set_index("security_id").reindex(assets)
    if not np.isfinite(current["close"].to_numpy(dtype=float)).all():
        return _blocked("RISK_DATA_UNAVAILABLE", "Every candidate and existing holding requires a cutoff price")
    mean = returns.mean().to_numpy(dtype=float)
    sample = np.atleast_2d(returns.cov().to_numpy(dtype=float))
    covariance = (1 - config.covariance_shrinkage) * sample + config.covariance_shrinkage * np.diag(np.diag(sample))
    previous = np.array([config.previous_weights.get(asset, 0) for asset in assets])
    count = len(assets)
    candidates = set(signals["security_id"].astype(str))
    caps = np.array([config.max_asset_weight if asset in candidates else 0 for asset in assets])
    rows, limits, names = [], [], []

    def add(name: str, weights: np.ndarray, maximum: float, trades: np.ndarray | None = None):
        rows.append(np.r_[weights, np.zeros(count) if trades is None else trades])
        limits.append(maximum)
        names.append(name)

    add("cash_min", np.ones(count), 1 - config.cash_min)
    add("cash_max", -np.ones(count), -(1 - config.cash_max))
    for index, asset in enumerate(assets):
        unit = np.zeros(count)
        unit[index] = 1
        add("buy_trade:" + asset, unit, previous[index], -unit)
        add("sell_trade:" + asset, -unit, -previous[index], -unit)
    if config.max_turnover is not None:
        add("turnover", np.zeros(count), config.max_turnover, np.ones(count))
    if config.industry_max_weights:
        if "industry" not in current or cast(pd.Series, current["industry"]).isna().to_numpy().any():
            return _blocked("INDUSTRY_UNAVAILABLE", "Historical industry is required for every holding/candidate")
        for industry, maximum in config.industry_max_weights.items():
            add("industry:" + industry, current["industry"].astype(str).eq(industry).to_numpy(dtype=float), maximum)
    for field, lower, upper in (("beta", config.beta_min, config.beta_max),
                                ("market_cap", config.log_market_cap_min, config.log_market_cap_max)):
        if lower is None and upper is None:
            continue
        if field not in current:
            return _blocked("RISK_FIELD_UNAVAILABLE", f"Real point-in-time {field} is required")
        exposure = cast(pd.Series, pd.to_numeric(current[field], errors="coerce")).to_numpy(dtype=float)
        if not np.isfinite(exposure).all() or (field == "market_cap" and (exposure <= 0).any()):
            return _blocked("RISK_FIELD_UNAVAILABLE", f"Invalid point-in-time {field}")
        if field == "market_cap":
            exposure = np.log(exposure)
        if upper is not None:
            add(field + "_max", exposure, upper)
        if lower is not None:
            add(field + "_min", -exposure, -lower)
    trade_caps = np.full(count, 2.0)
    if config.max_adv_participation is not None:
        if not {"raw_close", "volume"}.issubset(frame):
            return _blocked("ADV_UNAVAILABLE", "Raw close and real share volume are required")
        dollar_volume = frame.assign(dollar_volume=frame["raw_close"] * frame["volume"]).pivot(
            index="session", columns="security_id", values="dollar_volume").reindex(columns=assets).tail(20)
        if len(dollar_volume) < 20 or not np.isfinite(dollar_volume.to_numpy(dtype=float)).all():
            return _blocked("ADV_UNAVAILABLE", "Twenty complete historical dollar-volume sessions are required")
        adv = dollar_volume.mean().to_numpy(dtype=float)
        if (adv <= 0).any():
            return _blocked("ADV_UNAVAILABLE", "Historical dollar volume must be positive")
        trade_caps = config.max_adv_participation * adv / config.portfolio_nav
    matrix, bound = np.array(rows), np.array(limits)
    bounds = [(0.0, float(cap)) for cap in caps] + [(0.0, float(cap)) for cap in trade_caps]
    feasible = linprog(np.zeros(2 * count), A_ub=matrix, b_ub=bound, bounds=bounds, method="highs")
    if not feasible.success:
        return _blocked("CONSTRAINTS_INFEASIBLE", str(feasible.message))
    constraints = [{"type": "ineq", "fun": lambda x: bound - matrix @ x, "jac": lambda x: -matrix}]
    equal = np.array([(1 - config.cash_min) / max(len(candidates), 1) if asset in candidates else 0 for asset in assets])

    def objective(x, risk_aversion=config.risk_aversion, cost=config.transaction_cost_bps / 10_000):
        weights = x[:count]
        return float(risk_aversion * weights @ covariance @ weights - mean @ weights + cost * np.sum(x[count:]))

    baseline = minimize(lambda x: float(np.sum((x[:count] - equal) ** 2)), feasible.x, method="SLSQP",
                        bounds=bounds, constraints=constraints, options={"ftol": 1e-12, "maxiter": 500})
    baseline_valid = baseline.success and np.all(bound - matrix @ baseline.x >= -1e-7)
    baseline_x = np.asarray(baseline.x if baseline_valid else feasible.x)
    optimized = minimize(objective, baseline_x, method="SLSQP", bounds=bounds, constraints=constraints,
                         options={"ftol": 1e-12, "maxiter": 500})
    x = np.asarray(optimized.x)
    valid = optimized.success and np.all(bound - matrix @ x >= -1e-7)
    if not valid:
        x = baseline_x
    weights, baseline_weights = x[:count], baseline_x[:count]
    trades = np.abs(weights - previous)
    # Report residuals against economic trades, never slack auxiliary variables.
    actual = np.r_[weights, trades]
    residuals = {name: float(slack) for name, slack in zip(names, bound - matrix @ actual, strict=True)}
    residuals.update({"weight:" + asset: float(cap - weight) for asset, cap, weight in zip(assets, caps, weights, strict=True)})
    residuals.update({"adv:" + asset: float(cap - trade) for asset, cap, trade in zip(assets, trade_caps, trades, strict=True)})
    baseline_trades = np.abs(baseline_weights - previous)
    return {
        "status": "completed", "feasible": True, "method": "mean_variance_with_explicit_costs",
        "fallback_used": not valid, "solver_message": str(optimized.message),
        "target_weights": [{"security_id": asset, "target_weight": float(weight)} for asset, weight in zip(assets, weights, strict=True)],
        "cash_weight": float(1 - weights.sum()), "turnover": float(trades.sum()),
        "turnover_convention": "absolute_buy_and_sell_notional_over_nav",
        "constraint_residuals": residuals, "max_constraint_violation": max(0.0, -min(residuals.values())),
        "baseline": {"method": "feasible_equal_weight_projection" if baseline_valid else "feasible_vertex",
                     "target_weights": baseline_weights.tolist(),
                     "objective": objective(np.r_[baseline_weights, baseline_trades])},
        "objective": objective(actual), "expected_daily_return": float(mean @ weights),
        "estimated_daily_variance": float(weights @ covariance @ weights),
        "cost_sensitivity": [{"cost_bps": bps, "estimated_cost": float(bps / 10_000 * trades.sum()),
                              "baseline_estimated_cost": float(bps / 10_000 * baseline_trades.sum())}
                             for bps in sorted({0, config.transaction_cost_bps, 2 * config.transaction_cost_bps})],
        "estimate_start": cast(pd.Timestamp, returns.index[0]).date().isoformat(),
        "estimate_end": cast(pd.Timestamp, returns.index[-1]).date().isoformat(),
        "estimate_observations": len(returns), "covariance_shrinkage": config.covariance_shrinkage,
        "parameter_selection": "frozen_parameters", "out_of_sample_increment": None,
        "research_decision": "not_evaluated", "notice": "Research allocation; estimated utility is not realized performance",
    }


def _select_parameters(signals: pd.DataFrame, market_frame: pd.DataFrame,
                        config: PortfolioOptimizationConfig) -> dict:
    from quantgpt.research.contracts import SimulationConfigV1
    from quantgpt.research.ledger import simulate_target_weights

    if config.training_end is None or config.validation_end is None:
        raise ValueError("Parameter selection requires explicit training and validation endpoints")
    if config.previous_weights:
        return _blocked("SELECTION_START_STATE_UNSUPPORTED", "Parameter selection currently requires fresh cash")
    if not {"raw_open", "raw_close"}.issubset(market_frame):
        return _blocked("VALIDATION_RAW_PRICES_REQUIRED", "Validation uses next-open fills and raw-close valuation")
    frame = _panel(market_frame)
    training_end = pd.Timestamp(config.training_end)
    validation_end = pd.Timestamp(config.validation_end)
    sessions = pd.DatetimeIndex(sorted(frame["session"].unique()))
    validation_sessions = sessions[(sessions > training_end) & (sessions <= validation_end)]
    if validation_sessions.empty:
        return _blocked("EMPTY_VALIDATION_WINDOW", "No sessions after training_end and before validation_end")
    raw = frame.copy()
    raw["open"], raw["close"] = raw["raw_open"], raw["raw_close"]
    raw.attrs["adjustment"] = "raw"
    trials: list[dict] = []
    choices: list[tuple[float, float, dict]] = []
    for risk in dict.fromkeys(config.risk_aversion_candidates):
        fitted = optimize_portfolio(signals, market_frame, config.model_copy(update={
            "asof_session": config.training_end, "risk_aversion_candidates": (), "risk_aversion": risk,
        }))
        trial: dict = {"risk_aversion": risk, "training_result": fitted, "performance_observed": False}
        if fitted.get("feasible"):
            baseline = [{"security_id": item["security_id"], "target_weight": weight}
                        for item, weight in zip(fitted["target_weights"], fitted["baseline"]["target_weights"], strict=True)]

            def simulate(weights: list[dict], cost_bps: float) -> float:
                targets = pd.DataFrame(weights).rename(columns={"security_id": "stock_code"})
                targets["trade_date"] = training_end
                simulation = SimulationConfigV1(initial_cash=config.portfolio_nav, fees_bps=cost_bps,
                                                rebalance_anchor_session=cast(date, training_end.date()))
                ledger = simulate_target_weights(raw, targets, simulation, evaluation_start=validation_sessions[0],
                                                  evaluation_end=validation_end)
                return float((1 + ledger.returns).prod() - 1)

            try:
                candidate_return = simulate(fitted["target_weights"], config.transaction_cost_bps)
                trial.update({"performance_observed": True, "validation_net_return": candidate_return})
                baseline_return = simulate(baseline, config.transaction_cost_bps)
                trial.update({"performance_observed": True, "validation_net_return": candidate_return,
                              "baseline_validation_net_return": baseline_return,
                              "increment_vs_baseline": candidate_return - baseline_return,
                              "cost_sensitivity": [
                                  {"cost_bps": bps, "validation_net_return": simulate(fitted["target_weights"], bps),
                                   "baseline_validation_net_return": simulate(baseline, bps)}
                                  for bps in sorted({0, config.transaction_cost_bps, 2 * config.transaction_cost_bps})]})
                choices.append((candidate_return, -risk, trial))
            except ValueError as exc:
                trial["error"] = str(exc)
        trials.append(trial)
    if not choices:
        return {**_blocked("NO_FEASIBLE_PARAMETER_CANDIDATE", "Every parameter trial failed feasibility or valuation"),
                "parameter_trials": trials, "parameter_attempts": len(trials)}
    chosen = max(choices, key=lambda item: (item[0], item[1]))[2]
    final = optimize_portfolio(signals, market_frame, config.model_copy(update={
        "risk_aversion_candidates": (), "risk_aversion": chosen["risk_aversion"],
    }))
    return {**final, "parameter_selection": "train_fit_validation_select", "selected_risk_aversion": chosen["risk_aversion"],
            "parameter_trials": trials, "parameter_attempts": len(trials),
            "performance_observed_trials": sum(max(1, len(item.get("cost_sensitivity", [])))
                                                for item in trials if item["performance_observed"]),
            "trial_count_unit": "observed_risk_parameter_and_cost_configuration",
            "training_end": config.training_end.isoformat() if config.training_end else None,
            "validation_end": config.validation_end.isoformat() if config.validation_end else None,
            "validation_increment_vs_baseline": chosen["increment_vs_baseline"],
            "validation_is_independent_final": False, "out_of_sample_increment": None}


async def optimize_signal_reference(session: AsyncSession, actor_id: uuid.UUID, project_id: uuid.UUID,
                                      signal_ref: dict, config: PortfolioOptimizationConfig, *,
                                      snapshot_root: str | Path) -> dict:
    from quantgpt.research.evaluations import authorize_data_inputs

    await require_project_member(session, actor_id, project_id, write=True)
    reference = signal_ref.get("artifact_ref", signal_ref)
    artifact = await read_artifact(session, actor_id, project_id, uuid.UUID(reference["artifact_id"]))
    evaluation_id = reference.get("evaluation_id") or signal_ref.get("evaluation_ref", {}).get("evaluation_id")
    if (artifact["kind"] != "signal" or reference.get("content_sha256") != artifact["content_sha256"]
            or str(reference.get("project_id")) != str(project_id)):
        raise ValueError("SIGNAL_REFERENCE_INTEGRITY_MISMATCH")
    evaluation = await get_evaluation(session, actor_id, project_id, evaluation_id)
    frozen = verify_frozen_config(evaluation)
    if frozen.backend != "local" or artifact["metadata"]["evaluation_hash"] != evaluation.evaluation_hash:
        raise ValueError("SIGNAL_LINEAGE_MISMATCH")
    if not frozen.window.start_session <= config.asof_session <= frozen.window.end_session:
        raise ValueError("OPTIMIZATION_SESSION_OUTSIDE_EVALUATION")
    if frozen.window.phase == "final" and config.risk_aversion_candidates:
        raise ValueError("FINAL_WINDOW_PARAMETER_SELECTION_FORBIDDEN")
    await authorize_data_inputs(session, project_id, frozen)
    identity = content_hash("quantgpt.portfolio/v1", {"signal": reference, "config": config.model_dump(mode="json"),
                                                      "evaluation_hash": evaluation.evaluation_hash})
    cached = (await session.scalars(select(ExperimentArtifact).where(
        ExperimentArtifact.experiment_id == evaluation.experiment_id,
        ExperimentArtifact.artifact_type == "portfolio_optimization",
        ExperimentArtifact.payload["portfolio_hash"].as_string() == identity,
    ))).first()
    if cached is not None:
        saved = await read_artifact(session, actor_id, project_id, cast(uuid.UUID, cached.id))
        return {**saved["payload"], "artifact_ref": {"artifact_id": str(cached.id), "kind": saved["kind"],
                "project_id": str(project_id), "evaluation_id": evaluation_id, "content_sha256": saved["content_sha256"]}}
    panel = load_evaluation_panel(frozen, snapshot_root)
    result = await asyncio.to_thread(optimize_portfolio, pd.DataFrame(artifact["payload"]), panel, config)
    payload = {**result, "portfolio_hash": identity, "signal_ref": reference,
               "config": config.model_dump(mode="json"), "evaluation_id": evaluation_id,
               "split_hash": frozen.window.split_hash, "scope": frozen.window.phase,
               "evidence_status": "research_only", "local_strategy_eligible": False}
    await require_project_member(session, actor_id, project_id, write=True)
    output_ref = await write_artifact(session, evaluation, "portfolio_optimization", cast(dict[str, Any], payload))
    await session.commit()
    return {**payload, "artifact_ref": output_ref}
