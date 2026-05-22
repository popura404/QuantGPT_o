"""Promotion provenance gate tests."""

import pytest

from quantgpt.validation.promotion import (
    AUTO_FULL_NOT_PROMOTABLE,
    BIASED_DIRECTION_MODE,
    BOUNDARY_CANDIDATE,
    BOUNDARY_EXPORT,
    BOUNDARY_SUBMIT,
    DATA_SNAPSHOT_REQUIRED,
    DUPLICATED_OR_REDUNDANT_FACTOR,
    FAILED_FDR,
    FINAL_TEST_REQUIRED_FOR_PROMOTION,
    ROLLING_UNSTABLE,
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
        data_quality={
            "enabled": True,
            "after_rows": 100,
            "after_stocks": 10,
            "warnings": [],
            "issues": [],
            "data_snapshot_id": "ds_test",
        },
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
        data_quality={"enabled": True, "after_rows": 100, "after_stocks": 10, "data_snapshot_id": "ds_test"},
        rolling_validation={"score": 10, "windows": [{"window_index": 0}]},
        placebo_test={"passed": True, "details": {"perm_pass": True, "decay_ok": True, "shift_ics": {"5": 0.01}}},
    )

    with pytest.raises(PromotionGateError) as excinfo:
        assert_promotion_ready_for_boundary(provenance, BOUNDARY_EXPORT)

    assert ROLLING_UNSTABLE in excinfo.value.blockers


def test_biased_or_withheld_oos_result_returns_structured_policy_blockers():
    provenance = build_factor_validation_provenance(
        oos_result={
            "direction_policy": "auto_full",
            "train": {"metrics": {"long_short_sharpe": 1.0}},
            "valid": {"metrics": {"long_short_sharpe": 0.8}},
            "test": {"status": "withheld", "metrics": {}},
        },
        oos_score={"decision": "candidate", "score": 75},
        data_quality={"enabled": True, "after_rows": 100, "after_stocks": 10, "data_snapshot_id": "ds_test"},
        rolling_validation={"score": 70, "windows": [{"window_index": 0}]},
        placebo_test={"passed": True, "details": {"perm_pass": True, "decay_ok": True, "shift_ics": {"5": 0.01}}},
    )
    decision = evaluate_promotion_provenance(provenance, BOUNDARY_EXPORT)

    assert decision["allowed"] is False
    assert BIASED_DIRECTION_MODE in decision["blockers"]
    assert FINAL_TEST_REQUIRED_FOR_PROMOTION in decision["blockers"]


def test_multiple_testing_and_similarity_blockers_are_structured_when_supplied():
    provenance = build_factor_validation_provenance(
        oos_result={
            "direction_policy": "train_fixed",
            "train": {"metrics": {"long_short_sharpe": 1.0}},
            "valid": {"metrics": {"long_short_sharpe": 0.8}},
            "test": {"metrics": {"long_short_sharpe": 0.7}},
        },
        oos_score={"decision": "candidate", "score": 75},
        data_quality={"enabled": True, "after_rows": 100, "after_stocks": 10, "data_snapshot_id": "ds_test"},
        rolling_validation={"score": 70, "windows": [{"window_index": 0}]},
        placebo_test={"passed": True, "details": {"perm_pass": True, "decay_ok": True, "shift_ics": {"5": 0.01}}},
        multiple_testing={
            "passed": False,
            "blockers": [FAILED_FDR],
            "trial_counts": {"total_trials_in_project": 20, "trials_in_same_factor_family": 10},
        },
        similarity={
            "duplicated": True,
            "blockers": [DUPLICATED_OR_REDUNDANT_FACTOR],
            "max_similarity": 0.99,
        },
    )
    decision = evaluate_promotion_provenance(provenance, BOUNDARY_EXPORT)

    assert decision["allowed"] is False
    assert FAILED_FDR in decision["blockers"]
    assert DUPLICATED_OR_REDUNDANT_FACTOR in decision["blockers"]


def test_missing_data_snapshot_blocks_promotion():
    provenance = build_factor_validation_provenance(
        oos_result={
            "direction_policy": "train_fixed",
            "train": {"metrics": {"long_short_sharpe": 1.0}},
            "valid": {"metrics": {"long_short_sharpe": 0.8}},
            "test": {"metrics": {"long_short_sharpe": 0.7}},
        },
        oos_score={"decision": "candidate", "score": 75},
        data_quality={"enabled": True, "after_rows": 100, "after_stocks": 10},
        rolling_validation={"score": 70, "windows": [{"window_index": 0}]},
        placebo_test={"passed": True, "details": {"perm_pass": True, "decay_ok": True, "shift_ics": {"5": 0.01}}},
    )
    decision = evaluate_promotion_provenance(provenance, BOUNDARY_EXPORT)

    assert decision["allowed"] is False
    assert DATA_SNAPSHOT_REQUIRED in decision["blockers"]
