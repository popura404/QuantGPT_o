"""Strategy signal export schema validation."""

from __future__ import annotations

from typing import Any

STRATEGY_SIGNAL_V1 = "strategy_signal.v1"
NON_EXECUTION_NOTICE = "Candidate signal only. Not an order or automated trading instruction."

FORBIDDEN_EXECUTION_FIELDS = {
    "broker",
    "account",
    "api_key",
    "order",
    "order_id",
    "order_type",
    "execution",
    "execution_algo",
    "submit_order",
    "buy_volume",
    "sell_volume",
    "order_price",
}

REQUIRED_STRATEGY_SIGNAL_V1_FIELDS = {
    "schema_version",
    "strategy_id",
    "strategy_version",
    "experiment_id",
    "factor_hash",
    "created_at",
    "as_of",
    "market",
    "asset_class",
    "universe",
    "rebalance_frequency",
    "holding_period",
    "signal_type",
    "notice",
    "validation_summary",
    "risk_constraints",
    "signals",
}


def assert_no_forbidden_execution_fields(payload: Any) -> None:
    found = _find_forbidden(payload)
    if found:
        raise ValueError(f"Forbidden strategy signal execution fields: {sorted(found)}")


def validate_strategy_signal_v1(payload: dict[str, Any]) -> None:
    missing = sorted(REQUIRED_STRATEGY_SIGNAL_V1_FIELDS - set(payload))
    if missing:
        raise ValueError(f"strategy_signal.v1 missing required fields: {missing}")
    if payload.get("schema_version") != STRATEGY_SIGNAL_V1:
        raise ValueError("strategy signal schema_version must be strategy_signal.v1")
    if payload.get("notice") != NON_EXECUTION_NOTICE:
        raise ValueError("strategy signal notice does not match the required non-execution notice")
    if not payload.get("experiment_id"):
        raise ValueError("strategy_signal.v1 requires experiment_id")
    if not payload.get("factor_hash"):
        raise ValueError("strategy_signal.v1 requires factor_hash")
    validation_summary = payload.get("validation_summary")
    if not isinstance(validation_summary, dict):
        raise ValueError("strategy_signal.v1 validation_summary must be an object")
    if not validation_summary.get("oos_enabled"):
        raise ValueError("strategy_signal.v1 export requires OOS validation proof")
    if validation_summary.get("direction_policy") != "train_fixed":
        raise ValueError("strategy_signal.v1 export requires direction_policy=train_fixed")
    if not validation_summary.get("data_snapshot_id"):
        raise ValueError("strategy_signal.v1 export requires data_snapshot_id")
    if not validation_summary.get("promotion_gate_passed"):
        raise ValueError("strategy_signal.v1 export requires promotion_gate_passed=true")
    if not isinstance(payload.get("signals"), list):
        raise ValueError("strategy_signal.v1 signals must be a list")
    assert_no_forbidden_execution_fields(payload)


def _find_forbidden(value: Any, prefix: str = "") -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            path = f"{prefix}.{key_text}" if prefix else key_text
            if key_text in FORBIDDEN_EXECUTION_FIELDS:
                found.add(path)
            found.update(_find_forbidden(child, path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.update(_find_forbidden(child, f"{prefix}.{index}" if prefix else str(index)))
    return found
