"""StrategySpec validation service."""

from __future__ import annotations

import pandas as pd
from pydantic import ValidationError

from ..expression_parser import extract_components, parse_expression
from ..fundamental_data import ALL_FUNDAMENTAL_NAMES
from . import a_share_adapter as _a_share_adapter  # noqa: F401 - registers adapter
from .adapters import get_adapter
from .errors import (
    DATA_FIELD_UNSUPPORTED,
    EXPRESSION_INVALID,
    MARKET_UNSUPPORTED,
    RISK_SHORT_NOT_ALLOWED,
    RULE_UNSUPPORTED,
    SCHEMA_INVALID,
    SCHEMA_UNKNOWN_FIELD,
    StrategyValidationIssue,
    StrategyValidationResult,
)
from .spec import StrategySpecV0, StrategySpecV1, parse_strategy_spec


def _validation_dummy() -> pd.DataFrame:
    return pd.DataFrame({
        "open": [1.0, 2.0, 3.0],
        "high": [1.1, 2.1, 3.1],
        "low": [0.9, 1.9, 2.9],
        "close": [1.0, 2.0, 3.0],
        "volume": [100.0, 200.0, 300.0],
        "amount": [100.0, 400.0, 900.0],
        "pct_change": [0.0, 1.0, -0.5],
        "returns": [0.0, 1.0, -0.5],
        "vwap": [1.0, 2.0, 3.0],
        "cap": [100.0, 200.0, 300.0],
        "market_cap": [100.0, 200.0, 300.0],
        "industry": ["a", "a", "b"],
        "trade_date": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]),
        "stock_code": ["a", "b", "c"],
        **{name: [1.0, 1.1, 1.2] for name in ALL_FUNDAMENTAL_NAMES},
    })


def validate_strategy_spec(data: StrategySpecV0 | StrategySpecV1 | dict) -> StrategyValidationResult:
    issues: list[StrategyValidationIssue] = []
    spec: StrategySpecV0 | StrategySpecV1 | None = None

    try:
        spec = data if isinstance(data, (StrategySpecV0, StrategySpecV1)) else parse_strategy_spec(data)
    except ValidationError as exc:
        issues.extend(_issues_from_pydantic_errors(exc.errors()))
    except ValueError as exc:
        issues.append(StrategyValidationIssue(
            code=SCHEMA_UNKNOWN_FIELD,
            message=str(exc),
            hint="Remove execution, broker, account, Python, script, and callback fields from StrategySpec.",
        ))

    if spec is None:
        return StrategyValidationResult(False, issues)

    try:
        adapter = get_adapter(spec.market)
    except ValueError:
        return StrategyValidationResult(False, [
            StrategyValidationIssue(
                code=MARKET_UNSUPPORTED,
                message=f"Unsupported market: {spec.market}",
                path="market",
                hint="Call list_markets and choose a registered strategy market.",
            )
        ])

    caps = adapter.capabilities()
    if spec.universe not in caps.universes:
        issues.append(StrategyValidationIssue(
            code=MARKET_UNSUPPORTED,
            message=f"Unsupported universe for {spec.market}: {spec.universe}",
            path="universe",
            hint=f"Use one of: {', '.join(caps.universes)}.",
        ))
    if spec.risk_rules.allow_short and not caps.supports_short:
        issues.append(StrategyValidationIssue(
            code=RISK_SHORT_NOT_ALLOWED,
            message="A-share MVP adapter does not support short positions.",
            path="risk_rules.allow_short",
            hint="Set risk_rules.allow_short=false.",
        ))

    allowed_fields = {field.name for field in caps.data_fields}
    for idx, factor in enumerate(spec.factors):
        try:
            func = parse_expression(factor.expression, mode="local")
            func(_validation_dummy())
        except Exception as exc:
            issues.append(StrategyValidationIssue(
                code=EXPRESSION_INVALID,
                message=str(exc),
                path=f"factors.{idx}.expression",
                hint="Use a valid local factor expression such as rank(close / ts_mean(close, 20)).",
            ))

        components = extract_components(factor.expression)
        unsupported = sorted(components["fields"] - allowed_fields)
        if unsupported:
            issues.append(StrategyValidationIssue(
                code=DATA_FIELD_UNSUPPORTED,
                message=f"Unsupported data fields for {spec.market}: {unsupported}",
                path=f"factors.{idx}.expression",
                hint="Call list_data_fields before generating the expression.",
            ))

    return StrategyValidationResult(not issues, issues, spec)


def _issues_from_pydantic_errors(errors: list[dict]) -> list[StrategyValidationIssue]:
    issues: list[StrategyValidationIssue] = []
    for error in errors:
        loc = ".".join(str(part) for part in error.get("loc", ()))
        message = error.get("msg", "Invalid StrategySpec")
        error_type = error.get("type", "")
        code = SCHEMA_INVALID
        hint = "Fix the StrategySpec schema and retry validation."

        if error_type == "extra_forbidden":
            code = SCHEMA_UNKNOWN_FIELD
            hint = "Remove unknown fields; StrategySpec v0 uses extra='forbid'."
        elif loc == "market":
            code = MARKET_UNSUPPORTED
            hint = "Call list_markets and choose a registered strategy market."
        elif loc == "risk_rules.allow_short":
            code = RISK_SHORT_NOT_ALLOWED
            hint = "Set risk_rules.allow_short=false."
        elif "signal_export" in loc or "run_strategy_" in loc or "weighting" in loc:
            code = RULE_UNSUPPORTED
            hint = "This value is not supported by the selected StrategySpec schema version."
        elif loc == "factors":
            code = RULE_UNSUPPORTED
            hint = "StrategySpec v0 requires exactly one factor."

        issues.append(StrategyValidationIssue(code=code, message=message, path=loc, hint=hint))
    return issues
