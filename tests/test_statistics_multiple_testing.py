"""Trial-aware multiple-testing helper regressions."""

from quantgpt.statistics.multiple_testing import (
    benjamini_hochberg,
    bonferroni_adjust,
    bootstrap_mean_ci,
    multiple_testing_report,
)


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
