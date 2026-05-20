"""Promotion provenance gate tests."""

import pytest

from quantgpt.validation.promotion import (
    AUTO_FULL_NOT_PROMOTABLE,
    BOUNDARY_CANDIDATE,
    BOUNDARY_EXPORT,
    BOUNDARY_SUBMIT,
    PromotionGateError,
    assert_promotion_ready_for_boundary,
    build_factor_validation_provenance,
    evaluate_promotion_provenance,
    research_only_provenance,
)


def _ready_provenance() -> dict:
    return build_factor_validation_provenance(
        oos_result={
            "direction_policy": "train_fixed",
            "train": {"metrics": {"long_short_sharpe": 1.1, "direction_adjusted_rank_ic_mean": 0.04}},
            "valid": {"metrics": {"long_short_sharpe": 0.9, "direction_adjusted_rank_ic_mean": 0.03}},
            "test": {"metrics": {"long_short_sharpe": 0.8, "direction_adjusted_rank_ic_mean": 0.02}},
        },
        oos_score={"decision": "candidate", "score": 80, "grade": "A", "overfit_risk": "low"},
        data_quality={"enabled": True, "after_rows": 100, "after_stocks": 10, "warnings": [], "issues": []},
        rolling_validation={"score": 70, "windows": [{"window_index": 0}], "summary": {"n_windows": 1}},
        placebo_test={
            "name": "安慰剂检验",
            "passed": True,
            "details": {"perm_pass": True, "decay_ok": True, "shift_ics": {"5": 0.01}},
        },
    )


def test_ready_provenance_allows_candidate_submit_and_export():
    provenance = _ready_provenance()

    assert evaluate_promotion_provenance(provenance, BOUNDARY_CANDIDATE)["allowed"] is True
    assert evaluate_promotion_provenance(provenance, BOUNDARY_SUBMIT)["allowed"] is True
    assert evaluate_promotion_provenance(provenance, BOUNDARY_EXPORT)["allowed"] is True


def test_auto_full_research_provenance_blocks_promotion():
    provenance = research_only_provenance(source="auto_full")
    decision = evaluate_promotion_provenance(provenance, BOUNDARY_SUBMIT)

    assert decision["allowed"] is False
    assert AUTO_FULL_NOT_PROMOTABLE in decision["blockers"]


def test_failed_required_check_blocks_export():
    provenance = build_factor_validation_provenance(
        oos_result={
            "direction_policy": "train_fixed",
            "train": {"metrics": {}},
            "valid": {"metrics": {}},
            "test": {"metrics": {}},
        },
        oos_score={"decision": "candidate", "score": 75},
        data_quality={"enabled": True, "after_rows": 100, "after_stocks": 10},
        rolling_validation={"score": 10, "windows": [{"window_index": 0}]},
        placebo_test={"passed": True, "details": {"perm_pass": True, "decay_ok": True, "shift_ics": {"5": 0.01}}},
    )

    with pytest.raises(PromotionGateError) as excinfo:
        assert_promotion_ready_for_boundary(provenance, BOUNDARY_EXPORT)

    assert "ROLLING_WINDOW_FAILED" in excinfo.value.blockers
