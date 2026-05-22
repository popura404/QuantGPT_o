"""Trial-aware multiple-testing helpers."""

from __future__ import annotations

import math
import random
from statistics import mean
from typing import Sequence


def bonferroni_adjust(p_value: float, n_trials: int) -> float:
    """Return a Bonferroni-adjusted p-value capped at 1.0."""
    return min(1.0, max(0.0, float(p_value)) * max(1, int(n_trials)))


def benjamini_hochberg(p_values: Sequence[float], alpha: float = 0.05) -> list[dict]:
    """Compute Benjamini-Hochberg decisions while preserving original order."""
    if not p_values:
        return []
    m = len(p_values)
    ordered = sorted((max(0.0, min(1.0, float(p))), idx) for idx, p in enumerate(p_values))
    max_rank = 0
    for rank, (p_value, _) in enumerate(ordered, start=1):
        if p_value <= alpha * rank / m:
            max_rank = rank
    adjusted = [0.0] * m
    running = 1.0
    for rank, (p_value, idx) in reversed(list(enumerate(ordered, start=1))):
        running = min(running, p_value * m / rank)
        adjusted[idx] = min(1.0, running)
    accepted_indices = {idx for rank, (_, idx) in enumerate(ordered, start=1) if rank <= max_rank}
    return [
        {
            "p_value": max(0.0, min(1.0, float(p_value))),
            "adjusted_p_value": adjusted[idx],
            "passed_fdr": idx in accepted_indices,
            "alpha": alpha,
        }
        for idx, p_value in enumerate(p_values)
    ]


def bootstrap_mean_ci(
    values: Sequence[float],
    *,
    n_bootstrap: int = 1000,
    confidence: float = 0.95,
    seed: int = 0,
) -> dict:
    """Return a deterministic bootstrap confidence interval for the sample mean."""
    clean = [float(v) for v in values if _finite(v)]
    if not clean:
        return {"mean": 0.0, "lower": 0.0, "upper": 0.0, "n": 0, "confidence": confidence}
    rng = random.Random(seed)
    samples = []
    for _ in range(max(1, int(n_bootstrap))):
        draw = [clean[rng.randrange(len(clean))] for _ in clean]
        samples.append(mean(draw))
    samples.sort()
    lower_idx = int((1.0 - confidence) / 2.0 * (len(samples) - 1))
    upper_idx = int((1.0 + confidence) / 2.0 * (len(samples) - 1))
    return {
        "mean": mean(clean),
        "lower": samples[max(0, lower_idx)],
        "upper": samples[min(len(samples) - 1, upper_idx)],
        "n": len(clean),
        "confidence": confidence,
    }


def multiple_testing_report(
    *,
    p_value: float,
    trial_counts: dict,
    alpha: float = 0.05,
    family_p_values: Sequence[float] | None = None,
) -> dict:
    """Build a promotion-ready summary of trial-aware significance checks."""
    n_project = max(1, int(trial_counts.get("total_trials_in_project") or 1))
    n_family = max(1, int(trial_counts.get("trials_in_same_factor_family") or 1))
    bonferroni_project = bonferroni_adjust(p_value, n_project)
    bonferroni_family = bonferroni_adjust(p_value, n_family)
    fdr_inputs = list(family_p_values or [p_value])
    fdr = benjamini_hochberg(fdr_inputs, alpha=alpha)
    current_fdr = fdr[-1] if fdr else {"passed_fdr": False, "adjusted_p_value": 1.0}
    passed = (
        bonferroni_project <= alpha
        and bonferroni_family <= alpha
        and bool(current_fdr.get("passed_fdr"))
    )
    blockers = []
    if bonferroni_project > alpha or bonferroni_family > alpha or not current_fdr.get("passed_fdr"):
        blockers.append("FAILED_FDR")
    return {
        "p_value": max(0.0, min(1.0, float(p_value))),
        "alpha": alpha,
        "passed": passed,
        "blockers": blockers,
        "trial_counts": dict(trial_counts),
        "bonferroni_project_p": bonferroni_project,
        "bonferroni_family_p": bonferroni_family,
        "fdr": fdr,
    }


def _finite(value: float) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False
