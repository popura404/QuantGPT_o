"""Factor similarity helper regressions."""

from quantgpt.statistics.factor_similarity import (
    correlation_similarity,
    expression_token_similarity,
    factor_similarity_report,
)


def test_expression_token_similarity_normalizes_equivalent_tokens():
    assert expression_token_similarity("Rank( Close )", "rank(close)") == 1.0
    assert 0.0 < expression_token_similarity("rank(close)", "rank(open)") < 1.0


def test_correlation_similarity_uses_absolute_correlation():
    assert correlation_similarity([1, 2, 3], [2, 4, 6]) == 1.0
    assert correlation_similarity([1, 2, 3], [-2, -4, -6]) == 1.0
    assert correlation_similarity([1], [1]) is None


def test_factor_similarity_report_flags_redundant_factor_values():
    report = factor_similarity_report(
        "rank(close)",
        "rank(open)",
        candidate_values=[1, 2, 3, 4],
        reference_values=[2, 4, 6, 8],
        threshold=0.95,
    )

    assert report["duplicated"] is True
    assert "DUPLICATED_OR_REDUNDANT_FACTOR" in report["blockers"]
    assert report["factor_value_correlation"] == 1.0


def test_factor_similarity_report_allows_distinct_low_similarity_factor():
    report = factor_similarity_report(
        "rank(close)",
        "ts_mean(volume, 20)",
        candidate_values=[1, 2, 3, 4],
        reference_values=[4, 1, 3, 2],
        threshold=0.95,
    )

    assert report["duplicated"] is False
    assert report["blockers"] == []
