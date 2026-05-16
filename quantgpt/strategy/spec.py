"""StrategySpec v0 schema for the QuantGPT strategy MVP."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

FORBIDDEN_EXECUTION_FIELDS = {
    "execution",
    "broker",
    "order",
    "account",
    "api_key",
    "python_code",
    "script",
    "callback_url",
}


class StrictBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FactorSpec(StrictBaseModel):
    id: str = Field(..., min_length=1, max_length=80)
    expression: str = Field(..., min_length=1)
    direction: Literal["higher_is_better", "lower_is_better"]
    weight: float = 1.0

    @field_validator("weight")
    @classmethod
    def validate_weight(cls, value: float) -> float:
        if value != 1.0:
            raise ValueError("MVP factor weight must be 1.0")
        return value


class RankThresholdSignalRule(StrictBaseModel):
    type: Literal["rank_threshold"]
    long_quantile: float = Field(..., gt=0, le=1)


class EqualWeightPortfolioRule(StrictBaseModel):
    weighting: Literal["equal_weight"]
    rebalance_period: int = Field(..., ge=1, le=60)


class RiskRules(StrictBaseModel):
    allow_short: bool = False
    max_asset_weight: float = Field(..., gt=0, le=1)
    max_turnover: float | None = Field(None, ge=0, le=2)

    @field_validator("allow_short")
    @classmethod
    def reject_shorting(cls, value: bool) -> bool:
        if value:
            raise ValueError("MVP does not allow short positions")
        return value


class FixedBpsCostModel(StrictBaseModel):
    type: Literal["fixed_bps"]
    bps: float = Field(..., ge=0, le=1000)


class ValidationConfig(StrictBaseModel):
    min_history_days: int = Field(..., ge=30, le=5000)
    run_strategy_anti_overfit: bool = False
    run_strategy_rolling_validation: bool = False

    @field_validator("run_strategy_anti_overfit", "run_strategy_rolling_validation")
    @classmethod
    def reject_post_mvp_validation(cls, value: bool) -> bool:
        if value:
            raise ValueError("strategy anti-overfit and rolling validation are Post-MVP")
        return value


class OutputConfig(StrictBaseModel):
    report: bool = True
    signal_export: bool = False

    @field_validator("signal_export")
    @classmethod
    def reject_signal_export(cls, value: bool) -> bool:
        if value:
            raise ValueError("SignalExport is Post-MVP")
        return value


class StrategySpecV0(StrictBaseModel):
    """Executable A-share MVP strategy schema, not the final cross-market model."""

    schema_version: Literal["strategy_spec/v0"]
    name: str = Field(..., min_length=1, max_length=120)
    asset_class: Literal["equity"]
    market: Literal["a_share"]
    frequency: Literal["daily"]
    universe: Literal["small_scale", "hs300", "csi500", "csi1000", "csi2000"]
    factors: list[FactorSpec] = Field(..., min_length=1, max_length=1)
    signal_rules: RankThresholdSignalRule
    portfolio_rule: EqualWeightPortfolioRule
    risk_rules: RiskRules
    cost_model: FixedBpsCostModel
    validation: ValidationConfig
    outputs: OutputConfig

    @model_validator(mode="before")
    @classmethod
    def reject_forbidden_execution_fields(cls, data):
        if isinstance(data, dict):
            forbidden = FORBIDDEN_EXECUTION_FIELDS & set(data)
            if forbidden:
                raise ValueError(f"Forbidden execution fields: {sorted(forbidden)}")
        return data


def parse_strategy_spec(data: StrategySpecV0 | dict) -> StrategySpecV0:
    if isinstance(data, StrategySpecV0):
        return data
    return StrategySpecV0.model_validate(data)


def example_strategy_spec() -> dict:
    return {
        "schema_version": "strategy_spec/v0",
        "name": "simple_momentum_top_quantile",
        "asset_class": "equity",
        "market": "a_share",
        "frequency": "daily",
        "universe": "hs300",
        "factors": [
            {
                "id": "momentum_20d",
                "expression": "rank(close / ts_mean(close, 20))",
                "direction": "higher_is_better",
                "weight": 1.0,
            }
        ],
        "signal_rules": {
            "type": "rank_threshold",
            "long_quantile": 0.2,
        },
        "portfolio_rule": {
            "weighting": "equal_weight",
            "rebalance_period": 5,
        },
        "risk_rules": {
            "allow_short": False,
            "max_asset_weight": 0.05,
            "max_turnover": 0.8,
        },
        "cost_model": {
            "type": "fixed_bps",
            "bps": 30,
        },
        "validation": {
            "min_history_days": 252,
            "run_strategy_anti_overfit": False,
            "run_strategy_rolling_validation": False,
        },
        "outputs": {
            "report": True,
            "signal_export": False,
        },
    }
