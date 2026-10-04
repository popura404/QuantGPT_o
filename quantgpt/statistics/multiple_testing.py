"""Trial-aware multiple-testing helpers."""

from __future__ import annotations

import math
import random
from statistics import mean
from typing import Sequence


def bonferroni_adjust(p_value: float, n_trials: int) -> float:
    """Return a Bonferroni-adjusted p-value capped at 1.0."""
    if not _finite(p_value) or not 0 <= p_value <= 1 or n_trials < 1:
        raise ValueError("A finite p-value and positive registered trial count are required")
    return min(1.0, float(p_value) * int(n_trials))


def benjamini_hochberg(p_values: Sequence[float], alpha: float = 0.05) -> list[dict]:
    """Compute Benjamini-Hochberg decisions while preserving original order."""
    if not p_values:
        return []
    if not 0 < alpha < 1 or any(not _finite(p) or not 0 <= p <= 1 for p in p_values):
        raise ValueError("FDR requires finite p-values in [0, 1] and alpha in (0, 1)")
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
    block_length: int | None = None,
) -> dict:
    """Circular moving-block bootstrap retains within-block serial dependence."""
    if not 0 < confidence < 1 or n_bootstrap < 1:
        raise ValueError("Invalid bootstrap confidence or sample count")
    clean = [float(v) for v in values if _finite(v)]
    if not clean:
        return {"mean": 0.0, "lower": 0.0, "upper": 0.0, "n": 0, "confidence": confidence}
    rng = random.Random(seed)
    block = block_length if block_length is not None else max(1, math.ceil(len(clean) ** (1 / 3)))
    if not 1 <= block <= len(clean):
        raise ValueError("block_length must be in [1, n]")
    samples = []
    for _ in range(max(1, int(n_bootstrap))):
        draw = []
        while len(draw) < len(clean):
            start = rng.randrange(len(clean))
            draw.extend(clean[(start + offset) % len(clean)] for offset in range(block))
        samples.append(mean(draw[:len(clean)]))
    samples.sort()
    lower_idx = int((1.0 - confidence) / 2.0 * (len(samples) - 1))
    upper_idx = int((1.0 + confidence) / 2.0 * (len(samples) - 1))
    return {
        "mean": mean(clean),
        "lower": samples[max(0, lower_idx)],
        "upper": samples[min(len(samples) - 1, upper_idx)],
        "n": len(clean),
        "confidence": confidence,
        "method": "circular_moving_block_bootstrap",
        "block_length": block,
    }


def multiple_testing_report(
    *,
    p_value: float,
    trial_counts: dict,
    alpha: float = 0.05,
    family_p_values: Sequence[float] | None = None,
) -> dict:
    """Build a promotion-ready summary of trial-aware significance checks."""
    if any(not trial_counts.get(key) or int(trial_counts[key]) < 1
           for key in ("total_trials_in_project", "trials_in_same_factor_family")):
        return {"passed": False, "blockers": ["TRIAL_COUNT_UNAVAILABLE"], "trial_counts": dict(trial_counts),
                "p_value": p_value, "alpha": alpha, "fdr": []}
    n_project = int(trial_counts["total_trials_in_project"])
    n_family = int(trial_counts["trials_in_same_factor_family"])
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
