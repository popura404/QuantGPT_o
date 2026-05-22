"""Statistical controls for factor-promotion governance."""

from .factor_similarity import (
    correlation_similarity,
    expression_token_similarity,
    factor_similarity_report,
)
from .multiple_testing import (
    benjamini_hochberg,
    bonferroni_adjust,
    bootstrap_mean_ci,
    multiple_testing_report,
)

__all__ = [
    "benjamini_hochberg",
    "bonferroni_adjust",
    "bootstrap_mean_ci",
    "correlation_similarity",
    "expression_token_similarity",
    "factor_similarity_report",
    "multiple_testing_report",
]
