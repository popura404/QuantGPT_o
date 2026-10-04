"""Traceable descriptive research cards from already authorized evaluations.

This module never fetches data, evaluates an expression, reserves a holdout or
certifies a strategy. Callers must register all trials and authorize each window
through the project service before supplying these internal runner outputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import numpy as np
import pandas as pd

from quantgpt.research.contracts import (
    EvaluationConfigV1,
    FactorDefinitionV1,
    content_hash,
    definition_hash,
    evaluation_hash,
)
from quantgpt.research.ledger import LedgerResult
from quantgpt.statistics.multiple_testing import bootstrap_mean_ci


@dataclass(frozen=True)
class EvaluationEvidence:
    evaluation_id: str
    definition: FactorDefinitionV1
    config: EvaluationConfigV1
    output: dict[str, Any]

    @property
    def identity(self) -> str:
        return evaluation_hash(self.definition, self.config)


def _unavailable(reason: str) -> dict[str, Any]:
    return {"status": "unavailable", "reason": reason}


def _series(values: Any, config: EvaluationConfigV1) -> pd.Series:
    if values is None:
        return pd.Series(dtype=float)
    if not isinstance(values, pd.Series):
        raise ValueError("Evidence must be a session-indexed series")
    result = values.astype(float).copy()
    result.index = pd.DatetimeIndex(pd.to_datetime(result.index))
    if result.index.has_duplicates:
        raise ValueError("Duplicate evidence sessions")
    if len(result) and (pd.Timestamp(str(result.index.min())) < pd.Timestamp(config.window.start_session)
                        or pd.Timestamp(str(result.index.max())) > pd.Timestamp(config.window.end_session)):
        raise ValueError("Evidence outside the registered evaluation window")
    return result.sort_index()


def _records(series: pd.Series) -> list[dict[str, Any]]:
    return [{"session": pd.Timestamp(str(day)).isoformat(), "value": float(value) if np.isfinite(value) else None}
            for day, value in series.items()]


def _summary(series: pd.Series, *, horizon: int = 1) -> dict[str, Any]:
    values = series.to_numpy(dtype=float)
    values = values[np.isfinite(values)]
    if not len(values):
        return {"status": "insufficient_data", "n": 0, "mean": None, "interval": None}
    block = max(horizon, int(np.ceil(len(values) ** (1 / 3))))
    interval = None
    # Do not treat one or two overlapping blocks as independent evidence.
    if len(values) == len(series) and len(values) >= max(20, block * 4):
        interval = bootstrap_mean_ci(values.tolist(), block_length=block, seed=0)
    return {"status": "descriptive" if interval else "insufficient_data", "n": len(values),
            "mean": float(np.mean(values)), "interval": interval,
            "missing_observations": int(len(series) - len(values)),
            "interval_scope": "pointwise_unadjusted; serial moving blocks; not a promotion test"}


def _performance(series: pd.Series) -> dict[str, Any]:
    clean = series.dropna()
    if clean.empty:
        return _unavailable("No return observations")
    if not np.isfinite(clean.to_numpy(dtype=float)).all() or (clean < -1).any():
        raise ValueError("Invalid return evidence")
    wealth = (1 + clean).cumprod()
    volatility = float(np.std(clean.to_numpy(dtype=float), ddof=1)) if len(clean) > 1 else 0.
    return {"status": "descriptive", "n": len(clean), "total_return": float(wealth.iloc[-1] - 1),
            "mean_daily_return": float(clean.mean()), "daily_volatility": volatility,
            "annualized_sharpe_zero_cash_rate": float(clean.mean() / volatility * np.sqrt(252)) if volatility > 0 else None,
            "max_drawdown": float((wealth / wealth.cummax().clip(lower=1.) - 1).min()),
            "annualization_sessions": 252}


def _check_output(evidence: EvaluationEvidence) -> None:
    if not evidence.evaluation_id.strip():
        raise ValueError("A registered evaluation identity is required")
    if evidence.config.backend != "local":
        raise ValueError("Local research cards cannot reinterpret platform metrics")
    if evidence.output:
        if evidence.output.get("semantics_version") != evidence.config.engine.semantics_version:
            raise ValueError("Output semantics do not match the registered evaluation")
        simulation = evidence.config.simulation_config
        if simulation is None or evidence.output.get("simulation_config") != simulation.model_dump(mode="json"):
            raise ValueError("Output simulation does not match the registered evaluation")
        if evidence.output.get("holding_period") != evidence.config.window.label_horizon_sessions:
            raise ValueError("Output label horizon does not match the registered evaluation")


def _factor_frame(evidence: EvaluationEvidence) -> pd.DataFrame:
    frame = evidence.output.get("_factor_df")
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return pd.DataFrame(columns=["trade_date", "stock_code", "factor_value"])
    required = ["trade_date", "stock_code", "factor_value"]
    if any(name not in frame for name in required):
        raise ValueError("Factor evidence lacks security/session/value keys")
    frame = cast(pd.DataFrame, frame[required]).copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    if frame.duplicated(["trade_date", "stock_code"]).any():
        raise ValueError("Duplicate signal evidence keys")
    start, end = pd.Timestamp(evidence.config.window.start_session), pd.Timestamp(evidence.config.window.end_session)
    # Warmup may exist in a runner frame; it is never part of the reported sample.
    return cast(pd.DataFrame, frame[frame.trade_date.between(start, end)])


def build_factor_research_card(*, evaluation_id: str, definition: FactorDefinitionV1,
                               config: EvaluationConfigV1, output: dict[str, Any],
                               economic_hypothesis: str | None = None,
                               hypothesis_registration: dict[str, Any] | None = None,
                               trial_count: int | None = None) -> dict[str, Any]:
    """Summarize one run without manufacturing missing evidence or new trials.

    A supplied hypothesis is descriptive; preregistration must be proven by the
    project audit ledger. Trial count is the server's count, never a user claim.
    """
    evidence = EvaluationEvidence(evaluation_id, definition, config, output)
    _check_output(evidence)
    if hypothesis_registration is not None:
        from quantgpt.research.hypotheses import DOMAIN

        registered = {key: hypothesis_registration[key] for key in
                      ("schema_version", "definition_hash", "economic_hypothesis", "frozen_baseline")}
        if (registered["definition_hash"] != definition_hash(definition)
                or hypothesis_registration.get("registration_hash") != content_hash(DOMAIN, registered)):
            raise ValueError("Hypothesis registration does not match this definition")
        if economic_hypothesis is not None and economic_hypothesis != registered["economic_hypothesis"]:
            raise ValueError("Hypothesis text differs from its frozen registration")
        economic_hypothesis = registered["economic_hypothesis"]
    if trial_count is not None and trial_count < 1:
        raise ValueError("Observed trial count must be positive")
    values = _factor_frame(evidence)
    rank_ic = _series(output.get("_raw_rank_ic_series"), config)
    pearson_ic = _series(output.get("_raw_ic_series"), config)
    returns = _series(output.get("strategy_returns"), config)
    ic = _summary(rank_ic, horizon=config.window.label_horizon_sessions)
    groups: list[dict[str, Any]] = []
    for group_id, ledger in sorted(output.get("_group_ledgers", {}).items()):
        if not isinstance(ledger, LedgerResult):
            raise ValueError("Group evidence requires the actual simulation ledger")
        net, gross = _series(ledger.returns, config), _series(ledger.gross_returns, config)
        if not net.index.equals(gross.index):
            raise ValueError("Gross/net ledger session mismatch")
        groups.append({"group_id": str(group_id), "gross": _performance(gross), "net": _performance(net),
                       "gross_curve": _records(gross), "net_curve": _records(net),
                       "mean_daily_turnover": float(cast(pd.Series, ledger.turnover["turnover"]).mean()) if not ledger.turnover.empty else None,
                       "charged_fees": float(cast(pd.Series, ledger.trades["fee"]).sum()) if not ledger.trades.empty else 0.,
                       "skipped_sessions": int(cast(pd.Series, ledger.states["skipped"]).sum()) if not ledger.states.empty else 0})
    stability = [{"year": int(str(year)), **_performance(cast(pd.Series, sample))}
                 for year, sample in returns.groupby([pd.Timestamp(str(day)).year for day in returns.index])] if len(returns) else []
    interval = ic.get("interval")
    if not len(returns) or not len(rank_ic):
        conclusion = "insufficient_data"
    elif interval is None:
        conclusion = "insufficient_independent_observations"
    elif interval["lower"] <= 0 <= interval["upper"]:
        conclusion = "not_significant_at_pointwise_95_percent"
    else:
        conclusion = "descriptive_effect_requires_trial_correction_and_validation"
    blockers = ["VALIDATION_PROFILE_INCOMPLETE", "RISK_EXPOSURE_EVIDENCE_MISSING",
                "FROZEN_BASELINE_COMPARISON_MISSING", "MULTIPLE_TEST_CORRECTION_MISSING"]
    if config.evidence_eligibility == "research_only":
        blockers.append("DATA_OR_ENGINE_VERSION_UNVERIFIED")
    if not economic_hypothesis:
        blockers.append("ECONOMIC_HYPOTHESIS_NOT_REGISTERED")
    elif hypothesis_registration is None:
        blockers.append("HYPOTHESIS_REGISTRATION_REQUIRES_AUDIT_RECORD")
    payload: dict[str, Any] = {
        "schema_version": "factor_research_card/v1", "evaluation_id": evaluation_id,
        "evaluation_hash": evidence.identity, "definition_hash": definition_hash(definition),
        "backend": config.backend, "scope": config.window.phase, "research_scope": config.scope.model_dump(mode="json"),
        "window": config.window.model_dump(mode="json"), "simulation_config": config.simulation_config.model_dump(mode="json") if config.simulation_config else None,
        "data_inputs": [item.model_dump(mode="json") for item in config.data_inputs],
        "expression": definition.expression, "economic_hypothesis": economic_hypothesis,
        "hypothesis_registration": hypothesis_registration,
        "fields": [item.model_dump(mode="json") for item in definition.fields],
        "coverage": {"factor_rows": len(values), "securities": int(values.stock_code.nunique()),
                     "observed_sessions": int(values.trade_date.nunique()),
                     "finite_factor_rows": int(np.isfinite(values.factor_value.to_numpy(dtype=float)).sum()),
                     "historical_universe_completeness": "unverified"},
        "ic": {"horizon_sessions": config.window.label_horizon_sessions, "rank_ic": ic,
               "pearson_ic": _summary(pearson_ic, horizon=config.window.label_horizon_sessions),
               "rank_ic_curve": _records(rank_ic), "pearson_ic_curve": _records(pearson_ic),
               "direction": "raw_factor; no performance-selected sign"},
        "ic_decay": _unavailable("Requires separately registered horizon evaluations"),
        "groups": groups, "performance": _performance(returns), "net_return_curve": _records(returns),
        "cost_sensitivity": _unavailable("Requires separately registered cost evaluations; no constant penalty approximation"),
        "risk_exposures": _unavailable("Requires same-snapshot point-in-time exposures and actual position weights"),
        "historical_stability": {"status": "descriptive", "calendar_years": stability},
        "library_similarity": _unavailable("No registered reference signals and return streams supplied"),
        "incremental_contribution": _unavailable("No preregistered frozen portfolio baseline supplied"),
        "statistics": {"observed_project_trials": trial_count, "multiple_test_correction": "not_available",
                       "confidence_scope": "descriptive_pointwise", "selection_adjusted": False},
        "execution": {"status": "simulated" if groups else "unavailable",
                      "scope": "long_only_book; liquidity/corporate-action coverage requires external verification"},
        "conclusion": conclusion, "evidence_status": "research_only", "research_decision": "not_evaluated",
        "blockers": blockers,
    }
    payload["card_hash"] = content_hash("quantgpt.factor_research_card/v1", payload)
    return payload


def _comparable(left: EvaluationEvidence, right: EvaluationEvidence, *, variation: str = "none") -> None:
    _check_output(left)
    _check_output(right)
    a, b = left.config.model_dump(mode="json"), right.config.model_dump(mode="json")
    if variation == "horizon":
        for item in (a, b):
            for key in ("label_horizon_sessions", "purge_sessions"):
                item["window"].pop(key)
            item["simulation_config"].pop("rebalance_every_sessions")
    if variation == "cost":
        for item in (a, b):
            item["simulation_config"].pop("fees_bps")
            item["simulation_config"].pop("slippage_bps")
    if a != b:
        raise ValueError("Comparison requires the same data, universe, split, engine, execution and cost configuration")


def compare_evaluation_evidence(candidate: EvaluationEvidence, baseline: EvaluationEvidence, *,
                                registered_baseline_hash: str) -> dict[str, Any]:
    """Paired difference of an augmented portfolio against a frozen baseline.

    The project service must resolve registered_baseline_hash from its immutable
    pre-selection audit entry. A caller-provided hash is not authorization.
    """
    if baseline.identity != registered_baseline_hash:
        raise ValueError("Frozen baseline identity mismatch")
    _comparable(candidate, baseline)
    left, right = _series(candidate.output.get("strategy_returns"), candidate.config), _series(baseline.output.get("strategy_returns"), baseline.config)
    if left.empty or not left.index.equals(right.index) or left.isna().any() or right.isna().any():
        raise ValueError("Paired comparison requires complete matching return sessions")
    signals = []
    frames = [_factor_frame(item).set_index(["trade_date", "stock_code"]).sort_index() for item in (candidate, baseline)]
    if not frames[0].index.equals(frames[1].index):
        raise ValueError("Signal comparison requires the same security/session grid")
    merged = pd.concat([frames[0].factor_value.rename("candidate"), frames[1].factor_value.rename("baseline")], axis=1)
    for session, daily in merged.groupby(level="trade_date"):
        clean = daily.replace([np.inf, -np.inf], np.nan).dropna()
        correlation = clean.candidate.corr(clean.baseline, method="spearman") if len(clean) >= 3 else np.nan
        signals.append({"session": pd.Timestamp(str(session)).isoformat(),
                        "rank_correlation": float(correlation) if np.isfinite(correlation) else None, "n": len(clean)})
    correlation = left.corr(right) if left.std() > 0 and right.std() > 0 else np.nan
    delta = left - right
    return {"schema_version": "incremental_evidence/v1", "candidate_evaluation_id": candidate.evaluation_id,
            "baseline_evaluation_id": baseline.evaluation_id, "candidate_hash": candidate.identity,
            "frozen_baseline_hash": registered_baseline_hash, "scope": candidate.config.window.phase,
            "signal_similarity": signals, "return_correlation": float(correlation) if np.isfinite(correlation) else None,
            "paired_daily_net_return_difference": _summary(delta, horizon=candidate.config.window.label_horizon_sessions),
            "difference_curve": _records(delta), "candidate": _performance(left), "baseline": _performance(right),
            "interpretation": "paired augmented-portfolio minus frozen-baseline; not standalone factor alpha",
            "evidence_status": "research_only", "selection_adjusted": False,
            "required_service_guards": ["BASELINE_FROZEN_BEFORE_SELECTION", "ALL_TRIALS_RECORDED", "FINAL_WINDOW_EXPOSURE_RECORDED"]}


def compare_registered_variants(evidence: list[EvaluationEvidence], *, variation: str) -> dict[str, Any]:
    """Describe existing horizon/cost trials; never create unbudgeted reruns."""
    if variation not in {"horizon", "cost"} or len(evidence) < 2:
        raise ValueError("At least two registered cost or horizon variants are required")
    if len({item.identity for item in evidence}) != len(evidence):
        raise ValueError("Duplicate evaluation variants")
    base = evidence[0]
    results = []
    for item in evidence:
        if definition_hash(item.definition) != definition_hash(base.definition):
            raise ValueError("Variants require one frozen factor definition")
        _comparable(base, item, variation=variation)
        results.append({"evaluation_id": item.evaluation_id, "evaluation_hash": item.identity,
                        "horizon_sessions": item.config.window.label_horizon_sessions,
                        "simulation_config": item.config.simulation_config.model_dump(mode="json") if item.config.simulation_config else None,
                        "rank_ic": _summary(_series(item.output.get("_raw_rank_ic_series"), item.config), horizon=item.config.window.label_horizon_sessions),
                        "performance": _performance(_series(item.output.get("strategy_returns"), item.config))})
    return {"schema_version": "research_variants/v1", "variation": variation, "evaluations": results,
            "observed_trials_in_this_comparison": len(results), "scope": base.config.window.phase,
            "selection_adjusted": False, "evidence_status": "research_only",
            "required_service_guards": ["ALL_TRIALS_RECORDED", "FINAL_WINDOW_EXPOSURE_RECORDED"]}


# Templates, not observations or preregistered project trials.
STARTER_HYPOTHESES = (
    {"name": "momentum", "expression": "rank(close / delay(close, 20) - 1)",
     "economic_hypothesis": "Persistent information diffusion may produce continuation after the signal date.", "fields": ["close"]},
    {"name": "reversal", "expression": "-rank(close / delay(close, 5) - 1)",
     "economic_hypothesis": "Temporary price pressure may reverse; costs can erase the effect.", "fields": ["close"]},
    {"name": "liquidity", "expression": "rank(ts_mean(volume, 20))",
     "economic_hypothesis": "Trading activity proxies liquidity; raw share volume also reflects company size and split basis.", "fields": ["volume"]},
    {"name": "value", "expression": "rank(equity / market_cap)",
     "economic_hypothesis": "A lower price relative to known book equity may compensate for risk or reflect mispricing.",
     "fields": ["equity", "market_cap"], "capability_status": "unavailable_in_free_demo"},
)
