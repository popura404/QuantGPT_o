"""Trial-aware multiple-testing helper regressions."""

import pytest

from quantgpt.statistics.multiple_testing import (
    benjamini_hochberg,
    block_sign_test,
    bonferroni_adjust,
    bootstrap_mean_ci,
    multiple_testing_report,
)


@pytest.mark.parametrize("trials", [50, 51, 100])
def test_block_sign_tail_resolution_scales_with_trial_count(trials):
    result = block_sign_test([1.] * 24, trials)
    assert result["resolution_sufficient"] and result["adjusted_p_value"] <= .05
    assert result["p_value"] >= 1 / (result["randomizations"] + 1)
    assert block_sign_test([1., -1.] * 12, trials)["adjusted_p_value"] > .05


def test_few_independent_blocks_report_exact_resolution_limit():
    result = block_sign_test([1., 1., 1., 0.], 51)
    assert result["exact"] and not result["resolution_sufficient"]
    assert result["minimum_attainable_p"] == result["p_value"] == 1 / 8


def test_bonferroni_adjustment_caps_at_one():
    assert bonferroni_adjust(0.01, 3) == 0.03
    assert bonferroni_adjust(0.5, 3) == 1.0


def test_benjamini_hochberg_preserves_original_order():
    report = benjamini_hochberg([0.04, 0.001, 0.02], alpha=0.05)

    assert [row["p_value"] for row in report] == [0.04, 0.001, 0.02]
    assert report[1]["passed_fdr"] is True
    assert all("adjusted_p_value" in row for row in report)


def test_bootstrap_mean_ci_is_deterministic():
    first = bootstrap_mean_ci([1, 2, 3, 4], n_bootstrap=100, seed=7)
    second = bootstrap_mean_ci([1, 2, 3, 4], n_bootstrap=100, seed=7)

    assert first == second
    assert first["lower"] <= first["mean"] <= first["upper"]


def test_multiple_testing_report_blocks_weak_adjusted_significance():
    report = multiple_testing_report(
        p_value=0.03,
        trial_counts={
            "total_trials_in_project": 20,
            "trials_in_same_factor_family": 10,
        },
        family_p_values=[0.01, 0.02, 0.03],
    )

    assert report["passed"] is False
    assert "FAILED_FDR" in report["blockers"]
    assert report["bonferroni_project_p"] == 0.6
