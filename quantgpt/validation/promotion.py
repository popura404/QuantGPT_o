"""Promotion gate for factors that may enter candidate, submit, or export flows."""

from __future__ import annotations

from typing import Any

from .policy import (
    BIASED_DIRECTION_MODE,
    FINAL_TEST_REQUIRED_FOR_PROMOTION,
    OOS_SUMMARY_REQUIRED,
    SAFE_DIRECTION_POLICIES,
    classify_research_mode,
)

PROMOTION_SUITE_VERSION = "factor_validation/v1"
PROMOTION_READY = "promotion_ready"
RESEARCH_ONLY = "research_only"

BOUNDARY_CANDIDATE = "candidate"
BOUNDARY_SUBMIT = "submit"
BOUNDARY_EXPORT = "export"
PROMOTION_BOUNDARIES = (BOUNDARY_CANDIDATE, BOUNDARY_SUBMIT, BOUNDARY_EXPORT)

REQUIRED_CHECKS = (
    "data_snapshot",
    "data_quality",
    "train_valid_test",
    "rolling_window",
    "placebo",
    "time_shift",
)

AUTO_FULL_NOT_PROMOTABLE = "AUTO_FULL_NOT_PROMOTABLE"
VALIDATION_PROVENANCE_REQUIRED = "VALIDATION_PROVENANCE_REQUIRED"
VALIDATION_SUITE_FAILED = "VALIDATION_SUITE_FAILED"
FAILED_FDR = "FAILED_FDR"
FAILED_PSR = "FAILED_PSR"
FAILED_DSR = "FAILED_DSR"
ROLLING_UNSTABLE = "ROLLING_UNSTABLE"
DUPLICATED_OR_REDUNDANT_FACTOR = "DUPLICATED_OR_REDUNDANT_FACTOR"
TRIAL_COUNT_UNAVAILABLE = "TRIAL_COUNT_UNAVAILABLE"
DATA_SNAPSHOT_REQUIRED = "DATA_SNAPSHOT_REQUIRED"
ROLLING_WINDOW_MIN_SCORE = 60.0


class PromotionGateError(ValueError):
    """Raised when a candidate/submit/export boundary is reached without promotion proof."""

    def __init__(self, boundary: str, blockers: list[str]):
        self.boundary = boundary
        self.blockers = blockers
        super().__init__(
            f"Validation provenance is not promotion-ready for {boundary}: {', '.join(blockers)}"
        )


def check_payload(
    name: str,
    passed: bool,
    *,
    details: dict | None = None,
    error_code: str | None = None,
    error_codes: list[str] | None = None,
) -> dict:
    payload = {
        "name": name,
        "passed": bool(passed),
        "details": details or {},
    }
    if error_code:
        payload["error_code"] = error_code
    if error_codes:
        payload["error_codes"] = error_codes
    return payload


def research_only_provenance(
    *,
    source: str,
    reason_code: str = AUTO_FULL_NOT_PROMOTABLE,
    blockers: list[str] | None = None,
    checks: dict | None = None,
    scope: dict | None = None,
    params: dict | None = None,
) -> dict:
    final_blockers = blockers or [reason_code]
    provenance = {
        "suite_version": PROMOTION_SUITE_VERSION,
        "source": source,
        "promotion_state": RESEARCH_ONLY,
        "allowed_boundaries": [],
        "required_checks": list(REQUIRED_CHECKS),
        "blockers": final_blockers,
        "checks": checks or _missing_checks(),
    }
    if scope is not None:
        provenance["scope"] = scope
    if params is not None:
        provenance["params"] = params
        provenance.update(_policy_metadata(params))
    return provenance


def build_factor_validation_provenance(
    *,
    oos_result: dict,
    oos_score: dict,
    data_quality: dict | None,
    rolling_validation: dict | None,
    placebo_test: dict | None,
    multiple_testing: dict | None = None,
    similarity: dict | None = None,
    source: str = "validation_suite",
    scope: dict | None = None,
    target_scope: dict | None = None,
    params: dict | None = None,
) -> dict:
    """Build the mandatory validation proof for factor promotion."""
    checks = {
        "data_snapshot": _data_snapshot_check(oos_result, data_quality),
        "data_quality": _data_quality_check(data_quality),
        "train_valid_test": _train_valid_test_check(oos_result, oos_score),
        "rolling_window": _rolling_window_check(rolling_validation),
        "placebo": _placebo_check(placebo_test),
        "time_shift": _time_shift_check(placebo_test),
    }
    if multiple_testing is not None:
        checks["multiple_testing"] = _multiple_testing_check(multiple_testing)
    if similarity is not None:
        checks["similarity"] = _similarity_check(similarity)
    blockers: list[str] = []
    for name, check in checks.items():
        if not check.get("passed"):
            blockers.extend(_blockers_for_check(name, check))
    allowed = not blockers
    provenance = {
        "suite_version": PROMOTION_SUITE_VERSION,
        "source": source,
        "promotion_state": PROMOTION_READY if allowed else RESEARCH_ONLY,
        "allowed_boundaries": list(PROMOTION_BOUNDARIES) if allowed else [],
        "required_checks": list(REQUIRED_CHECKS),
        "blockers": blockers,
        "checks": checks,
    }
    if scope is not None:
        provenance["scope"] = scope
    if target_scope is not None:
        provenance["target_scope"] = target_scope
    if params is not None:
        provenance["params"] = params
        provenance.update(_policy_metadata(params))
    return provenance


def evaluate_promotion_provenance(provenance: dict | None, boundary: str) -> dict:
    """Return an allow/block decision for a promotion boundary."""
    if boundary not in PROMOTION_BOUNDARIES:
        raise ValueError(f"Unknown promotion boundary: {boundary}")
    if not isinstance(provenance, dict):
        return {
            "allowed": False,
            "boundary": boundary,
            "error_code": VALIDATION_PROVENANCE_REQUIRED,
            "blockers": [VALIDATION_PROVENANCE_REQUIRED],
        }

    blockers = list(provenance.get("blockers") or [])
    raw_checks = provenance.get("checks")
    checks: dict[str, dict] = raw_checks if isinstance(raw_checks, dict) else {}
    missing = [name for name in REQUIRED_CHECKS if name not in checks]
    failed: list[str] = []
    for name in REQUIRED_CHECKS:
        if name in checks and not checks[name].get("passed"):
            failed.extend(_blockers_for_check(name, checks[name]))
    blockers.extend(missing)
    blockers.extend(failed)
    allowed_boundaries = provenance.get("allowed_boundaries") or []
    if boundary not in allowed_boundaries:
        blockers.append(f"BOUNDARY_{boundary.upper()}_NOT_ALLOWED")

    # Preserve order while deduplicating.
    unique_blockers = list(dict.fromkeys(str(blocker) for blocker in blockers if blocker))
    return {
        "allowed": not unique_blockers and provenance.get("promotion_state") == PROMOTION_READY,
        "boundary": boundary,
        "error_code": None if not unique_blockers else VALIDATION_SUITE_FAILED,
        "blockers": unique_blockers,
    }


def assert_promotion_ready_for_boundary(provenance: dict | None, boundary: str) -> None:
    decision = evaluate_promotion_provenance(provenance, boundary)
    if not decision["allowed"]:
        raise PromotionGateError(boundary, decision["blockers"])


def testcase_to_dict(test_result: Any) -> dict | None:
    """Normalize AntiOverfitDetector TestResult/dataclass output to a plain dict."""
    if test_result is None:
        return None
    if isinstance(test_result, dict):
        return test_result
    return {
        "name": getattr(test_result, "name", ""),
        "passed": bool(getattr(test_result, "passed", False)),
        "details": getattr(test_result, "details", {}) or {},
    }


def _missing_checks() -> dict:
    return {
        name: check_payload(name, False, error_code=f"{name.upper()}_NOT_RUN")
        for name in REQUIRED_CHECKS
    }


def _data_quality_check(data_quality: dict | None) -> dict:
    if not isinstance(data_quality, dict):
        return check_payload("data_quality", False, error_code="DATA_QUALITY_NOT_RUN")
    enabled = data_quality.get("enabled") is True
    after_rows = _num(data_quality.get("after_rows"), 0.0)
    after_stocks = _num(data_quality.get("after_stocks"), 0.0)
    passed = enabled and after_rows > 0 and after_stocks > 0
    return check_payload(
        "data_quality",
        passed,
        details={
            "enabled": data_quality.get("enabled"),
            "mode": data_quality.get("mode"),
            "after_rows": data_quality.get("after_rows"),
            "after_stocks": data_quality.get("after_stocks"),
            "dropped_rows": data_quality.get("dropped_rows"),
            "dropped_stocks": data_quality.get("dropped_stocks"),
            "warnings": data_quality.get("warnings", []),
            "issues": data_quality.get("issues", []),
        },
        error_code=None if passed else "DATA_QUALITY_GATE_FAILED",
    )


def _data_snapshot_check(oos_result: dict | None, data_quality: dict | None) -> dict:
    oos_snapshot = oos_result.get("data_snapshot_id") if isinstance(oos_result, dict) else None
    quality_snapshot = data_quality.get("data_snapshot_id") if isinstance(data_quality, dict) else None
    snapshot_id = oos_snapshot or quality_snapshot
    passed = isinstance(snapshot_id, str) and bool(snapshot_id)
    return check_payload(
        "data_snapshot",
        passed,
        details={
            "data_snapshot_id": snapshot_id,
            "oos_snapshot_id": oos_snapshot,
            "data_quality_snapshot_id": quality_snapshot,
        },
        error_code=None if passed else DATA_SNAPSHOT_REQUIRED,
    )


def _train_valid_test_check(oos_result: dict, oos_score: dict) -> dict:
    has_windows = all(
        isinstance(oos_result.get(name), dict)
        and isinstance(oos_result[name].get("metrics"), dict)
        for name in ("train", "valid", "test")
    )
    safe_direction = oos_result.get("direction_policy") in SAFE_DIRECTION_POLICIES
    final_test = _has_final_test_metrics(oos_result)
    candidate = oos_score.get("decision") == "candidate"
    passed = has_windows and safe_direction and final_test and candidate
    error_codes: list[str] = []
    if not has_windows:
        error_codes.append(OOS_SUMMARY_REQUIRED)
    if not safe_direction:
        error_codes.append(BIASED_DIRECTION_MODE)
    if not final_test:
        error_codes.append(FINAL_TEST_REQUIRED_FOR_PROMOTION)
    if not candidate:
        error_codes.append("TRAIN_VALID_TEST_FAILED")
    return check_payload(
        "train_valid_test",
        passed,
        details={
            "has_train_valid_test": has_windows,
            "direction_policy": oos_result.get("direction_policy"),
            "final_test_ready": final_test,
            "decision": oos_score.get("decision"),
            "score": oos_score.get("score"),
            "grade": oos_score.get("grade"),
            "overfit_risk": oos_score.get("overfit_risk"),
        },
        error_codes=None if passed else error_codes,
    )


def _rolling_window_check(rolling_validation: dict | None) -> dict:
    if not isinstance(rolling_validation, dict):
        return check_payload("rolling_window", False, error_code="ROLLING_WINDOW_NOT_RUN")
    score = _num(rolling_validation.get("score"), 0.0)
    windows = rolling_validation.get("windows") or []
    passed = score >= ROLLING_WINDOW_MIN_SCORE and len(windows) > 0
    return check_payload(
        "rolling_window",
        passed,
        details={
            "score": rolling_validation.get("score"),
            "min_score": ROLLING_WINDOW_MIN_SCORE,
            "n_windows": len(windows),
            "summary": rolling_validation.get("summary", {}),
            "decay_analysis": rolling_validation.get("decay_analysis", {}),
        },
        error_code=None if passed else ROLLING_UNSTABLE,
    )


def _placebo_check(placebo_test: dict | None) -> dict:
    if not isinstance(placebo_test, dict):
        return check_payload("placebo", False, error_code="PLACEBO_NOT_RUN")
    raw_details = placebo_test.get("details")
    details: dict = raw_details if isinstance(raw_details, dict) else {}
    passed = bool(details.get("perm_pass"))
    return check_payload(
        "placebo",
        passed,
        details={
            "test_passed": placebo_test.get("passed"),
            "real_ic": details.get("real_ic"),
            "perm_95th": details.get("perm_95th"),
            "perm_pass": details.get("perm_pass"),
        },
        error_code=None if passed else "PLACEBO_FAILED",
    )


def _time_shift_check(placebo_test: dict | None) -> dict:
    if not isinstance(placebo_test, dict):
        return check_payload("time_shift", False, error_code="TIME_SHIFT_NOT_RUN")
    raw_details = placebo_test.get("details")
    details: dict = raw_details if isinstance(raw_details, dict) else {}
    shift_ics = details.get("shift_ics") or {}
    passed = bool(details.get("decay_ok")) and bool(shift_ics)
    return check_payload(
        "time_shift",
        passed,
        details={
            "test_passed": placebo_test.get("passed"),
            "decay_ok": details.get("decay_ok"),
            "shift_ics": shift_ics,
        },
        error_code=None if passed else "TIME_SHIFT_FAILED",
    )


def _multiple_testing_check(multiple_testing: dict) -> dict:
    trial_counts = multiple_testing.get("trial_counts")
    has_trials = isinstance(trial_counts, dict) and int(trial_counts.get("total_trials_in_project") or 0) > 0
    passed = bool(multiple_testing.get("passed")) and has_trials
    error_codes: list[str] = []
    if not has_trials:
        error_codes.append(TRIAL_COUNT_UNAVAILABLE)
    if not multiple_testing.get("passed"):
        error_codes.extend(str(code) for code in multiple_testing.get("blockers") or [FAILED_FDR])
    return check_payload(
        "multiple_testing",
        passed,
        details=multiple_testing,
        error_codes=None if passed else list(dict.fromkeys(error_codes)),
    )


def _similarity_check(similarity: dict) -> dict:
    duplicated = bool(similarity.get("duplicated"))
    passed = not duplicated
    blockers = similarity.get("blockers") or [DUPLICATED_OR_REDUNDANT_FACTOR]
    return check_payload(
        "similarity",
        passed,
        details=similarity,
        error_codes=None if passed else [str(blocker) for blocker in blockers],
    )


def _blocker_for_check(name: str, check: dict) -> str:
    blockers = _blockers_for_check(name, check)
    return blockers[0] if blockers else f"{name.upper()}_FAILED"


def _blockers_for_check(name: str, check: dict) -> list[str]:
    codes = check.get("error_codes")
    if isinstance(codes, list) and codes:
        return [str(code) for code in codes if code]
    return [str(check.get("error_code") or f"{name.upper()}_FAILED")]


def _has_final_test_metrics(oos_result: dict) -> bool:
    test = oos_result.get("test")
    if not isinstance(test, dict) or test.get("status") == "withheld":
        return False
    metrics = test.get("metrics")
    return isinstance(metrics, dict) and bool(metrics)


def _policy_metadata(params: dict) -> dict:
    policy = classify_research_mode(params)
    return {
        "research_mode": policy["research_mode"],
        "direction_policy": policy["direction_policy"],
        "formal_safe": policy["formal_safe"],
        "final_test_policy": policy["final_test_policy"],
    }


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
