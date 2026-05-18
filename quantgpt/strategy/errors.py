"""Strategy validation errors and result contracts."""

from __future__ import annotations

from dataclasses import dataclass, field

from .spec import StrategySpecV0, StrategySpecV1

SCHEMA_INVALID = "SCHEMA_INVALID"
SCHEMA_UNKNOWN_FIELD = "SCHEMA_UNKNOWN_FIELD"
EXPRESSION_INVALID = "EXPRESSION_INVALID"
DATA_FIELD_UNSUPPORTED = "DATA_FIELD_UNSUPPORTED"
MARKET_UNSUPPORTED = "MARKET_UNSUPPORTED"
RISK_SHORT_NOT_ALLOWED = "RISK_SHORT_NOT_ALLOWED"
RULE_UNSUPPORTED = "RULE_UNSUPPORTED"


@dataclass(slots=True)
class StrategyValidationIssue:
    code: str
    message: str
    path: str = ""
    hint: str = ""

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "path": self.path,
            "hint": self.hint,
        }


@dataclass(slots=True)
class StrategyValidationResult:
    is_valid: bool
    issues: list[StrategyValidationIssue] = field(default_factory=list)
    spec: StrategySpecV0 | StrategySpecV1 | None = None

    def to_dict(self) -> dict:
        return {
            "is_valid": self.is_valid,
            "issues": [issue.to_dict() for issue in self.issues],
        }


class StrategyValidationError(ValueError):
    def __init__(self, issues: list[StrategyValidationIssue]):
        self.issues = issues
        message = "; ".join(f"{issue.code}: {issue.message}" for issue in issues)
        super().__init__(message or "Strategy validation failed")
