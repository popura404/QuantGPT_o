"""Factor similarity helpers for duplicate/redundancy checks."""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd

from ..expression_parser import normalize_expression
from ..search_ledger import family_key

_TOKEN_PATTERN = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*|[-+]?\d+(?:\.\d+)?")


def expression_token_similarity(left: str, right: str) -> float:
    """Return Jaccard similarity of normalized expression tokens."""
    left_tokens = set(_TOKEN_PATTERN.findall(normalize_expression(left or "")))
    right_tokens = set(_TOKEN_PATTERN.findall(normalize_expression(right or "")))
    if not left_tokens and not right_tokens:
        return 1.0
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def correlation_similarity(left: Sequence[float] | pd.Series, right: Sequence[float] | pd.Series) -> float | None:
    """Return absolute Pearson correlation, ignoring non-finite aligned values."""
    left_series = pd.Series(left, dtype="float64")
    right_series = pd.Series(right, dtype="float64")
    aligned = pd.concat([left_series, right_series], axis=1).replace([np.inf, -np.inf], np.nan).dropna()
    if len(aligned) < 2:
        return None
    corr = aligned.iloc[:, 0].corr(aligned.iloc[:, 1])
    if corr is None or not math.isfinite(float(corr)):
        return None
    return abs(float(corr))


def factor_similarity_report(
    candidate_expression: str,
    reference_expression: str,
    *,
    candidate_values: Sequence[float] | pd.Series | None = None,
    reference_values: Sequence[float] | pd.Series | None = None,
    candidate_returns: Sequence[float] | pd.Series | None = None,
    reference_returns: Sequence[float] | pd.Series | None = None,
    threshold: float = 0.95,
) -> dict[str, Any]:
    """Build a structured duplicate-factor similarity report."""
    exact_expression = normalize_expression(candidate_expression or "") == normalize_expression(reference_expression or "")
    same_family = family_key(candidate_expression or "") == family_key(reference_expression or "")
    token_similarity = expression_token_similarity(candidate_expression, reference_expression)
    value_correlation = (
        correlation_similarity(candidate_values, reference_values)
        if candidate_values is not None and reference_values is not None
        else None
    )
    return_correlation = (
        correlation_similarity(candidate_returns, reference_returns)
        if candidate_returns is not None and reference_returns is not None
        else None
    )
    scores = [
        token_similarity,
        value_correlation if value_correlation is not None else 0.0,
        return_correlation if return_correlation is not None else 0.0,
    ]
    max_similarity = max(scores)
    duplicated = exact_expression or max_similarity >= threshold
    blockers = ["DUPLICATED_OR_REDUNDANT_FACTOR"] if duplicated else []
    return {
        "duplicated": duplicated,
        "blockers": blockers,
        "threshold": threshold,
        "exact_expression": exact_expression,
        "same_family": same_family,
        "expression_token_similarity": token_similarity,
        "factor_value_correlation": value_correlation,
        "return_stream_correlation": return_correlation,
        "max_similarity": max_similarity,
    }
