"""Shared formal/exploratory validation policy helpers."""

from __future__ import annotations

from typing import Any

BIASED_DIRECTION_MODE = "BIASED_DIRECTION_MODE"
OOS_SUMMARY_REQUIRED = "OOS_SUMMARY_REQUIRED"
FINAL_TEST_REQUIRED_FOR_PROMOTION = "FINAL_TEST_REQUIRED_FOR_PROMOTION"

FORMAL_SELECTION = "formal_selection"
FORMAL_FINAL = "formal_final"
EXPLORATORY = "exploratory"

PROMOTION_BOUNDARIES = {"candidate", "submit", "export"}
SAFE_DIRECTION_POLICIES = {"train_fixed", "fixed_positive", "fixed_negative", "manual"}


class FormalPolicyError(ValueError):
    """Raised when a payload is unsafe for a formal research boundary."""

    def __init__(self, boundary: str, blockers: list[str]):
        self.boundary = boundary
        self.blockers = blockers
        super().__init__(f"Payload is not formal-safe for {boundary}: {', '.join(blockers)}")


def classify_research_mode(params: dict | None) -> dict[str, Any]:
    """Classify a backtest/scoring request without changing legacy request fields."""
    params = params or {}
    oos_enabled = bool(params.get("oos_enabled"))
    validation_stage = str(params.get("validation_stage") or "selection")
    direction_mode = str(params.get("direction_mode") or "auto_full")
    fixed_direction = params.get("fixed_direction")

    if oos_enabled:
        final_stage = validation_stage == "final"
        return {
            "research_mode": FORMAL_FINAL if final_stage else FORMAL_SELECTION,
            "direction_policy": "train_fixed",
            "formal_safe": True,
            "final_test_policy": "executed" if final_stage else "withheld_until_validation_stage_final",
            "policy_blockers": [] if final_stage else [FINAL_TEST_REQUIRED_FOR_PROMOTION],
        }

    direction_policy = _direction_policy_for_legacy_mode(direction_mode, fixed_direction)
    blockers = [OOS_SUMMARY_REQUIRED]
    if direction_policy == "auto_full":
        blockers.insert(0, BIASED_DIRECTION_MODE)
    return {
        "research_mode": EXPLORATORY,
        "direction_policy": direction_policy,
        "formal_safe": False,
        "final_test_policy": "not_run",
        "policy_blockers": blockers,
    }


def explain_direction_policy(payload: dict | None) -> dict[str, Any]:
    """Return a compact explanation of the direction policy used by a result payload."""
    payload_dict: dict[str, Any] = payload or {}
    raw_params = payload_dict.get("params")
    params: dict[str, Any] = raw_params if isinstance(raw_params, dict) else {}
    policy = classify_research_mode(params)
    raw_oos_result = payload_dict.get("oos_result")
    oos_result: dict[str, Any] = raw_oos_result if isinstance(raw_oos_result, dict) else {}
    direction_policy = payload_dict.get("direction_policy") or oos_result.get("direction_policy") or policy["direction_policy"]
    direction_source = oos_result.get("direction_source") or payload_dict.get("direction_source")
    explanation = {
        "direction_policy": direction_policy,
        "direction_source": direction_source,
        "research_mode": payload_dict.get("research_mode") or policy["research_mode"],
        "formal_safe": bool(payload_dict.get("formal_safe", policy["formal_safe"])),
    }
    if direction_policy == "train_fixed":
        explanation["reason"] = "Direction was selected on the training window and reused out of sample."
    elif direction_policy == "auto_full":
        explanation["reason"] = "Direction may depend on the full sample and is exploratory only."
    else:
        explanation["reason"] = "Direction was supplied before formal evaluation and requires recorded rationale."
    return explanation


def assert_formal_safe(payload: dict | None, boundary: str) -> None:
    """Raise when a result cannot be used at a formal candidate/submit/export boundary."""
    blockers = formal_policy_blockers(payload, boundary)
    if blockers:
        raise FormalPolicyError(boundary, blockers)


def formal_policy_blockers(payload: dict | None, boundary: str) -> list[str]:
    payload_dict: dict[str, Any] = payload or {}
    raw_params = payload_dict.get("params")
    params: dict[str, Any] = raw_params if isinstance(raw_params, dict) else {}
    policy = classify_research_mode(params)
    blockers: list[str] = []

    raw_oos_result = payload_dict.get("oos_result")
    oos_result: dict[str, Any] = raw_oos_result if isinstance(raw_oos_result, dict) else {}
    direction_policy = payload_dict.get("direction_policy") or oos_result.get("direction_policy") or policy["direction_policy"]
    if direction_policy not in SAFE_DIRECTION_POLICIES:
        blockers.append(BIASED_DIRECTION_MODE)

    if not oos_result or not _has_train_valid_test(oos_result):
        blockers.append(OOS_SUMMARY_REQUIRED)

    if boundary in PROMOTION_BOUNDARIES and not _has_final_test_metrics(oos_result):
        blockers.append(FINAL_TEST_REQUIRED_FOR_PROMOTION)

    return list(dict.fromkeys(blockers))


def _direction_policy_for_legacy_mode(direction_mode: str, fixed_direction: Any) -> str:
    if direction_mode == "fixed" and fixed_direction == 1:
        return "fixed_positive"
    if direction_mode == "fixed" and fixed_direction == -1:
        return "fixed_negative"
    if direction_mode == "manual":
        return "manual"
    return "auto_full"


def _has_train_valid_test(oos_result: dict[str, Any]) -> bool:
    return all(isinstance(oos_result.get(name), dict) for name in ("train", "valid", "test"))


def _has_final_test_metrics(oos_result: dict[str, Any]) -> bool:
    test = oos_result.get("test")
    if not isinstance(test, dict):
        return False
    if test.get("status") == "withheld":
        return False
    metrics = test.get("metrics")
    return isinstance(metrics, dict) and bool(metrics)
