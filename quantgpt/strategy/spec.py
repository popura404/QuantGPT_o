"""StrategySpec schemas for the QuantGPT strategy framework."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

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


class FactorSpecV1(StrictBaseModel):
    id: str = Field(..., min_length=1, max_length=80)
    expression: str = Field(..., min_length=1)
    direction: Literal["higher_is_better", "lower_is_better"]
    weight: float = Field(1.0, gt=0)


class RankThresholdSignalRule(StrictBaseModel):
    type: Literal["rank_threshold"]
    long_quantile: float = Field(..., gt=0, le=1)


class RankThresholdSignalRuleV1(StrictBaseModel):
    type: Literal["rank_threshold"]
    long_quantile: float | None = Field(None, gt=0, le=1)
    top_n: int | None = Field(None, ge=1)

    @model_validator(mode="after")
    def validate_selection_mode(self):
        if (self.long_quantile is None) == (self.top_n is None):
            raise ValueError("StrategySpec v1 requires exactly one of long_quantile or top_n")
        return self


class EqualWeightPortfolioRule(StrictBaseModel):
    weighting: Literal["equal_weight"]
    rebalance_period: int = Field(..., ge=1, le=60)


class PortfolioRuleV1(StrictBaseModel):
    weighting: Literal["equal_weight", "score_weighted"]
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


class ValidationConfigV1(StrictBaseModel):
    min_history_days: int = Field(..., ge=30, le=5000)
    run_strategy_anti_overfit: bool = False
    run_strategy_rolling_validation: bool = False
    oos: OOSValidationConfigV1 | None = None
    data_quality: DataQualityValidationConfigV1 | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_forbidden_validation_fields(cls, data):
        if isinstance(data, dict):
            forbidden = _find_forbidden_keys(data)
            if forbidden:
                raise ValueError(f"Forbidden execution fields in validation: {sorted(forbidden)}")
        return data


class OOSValidationConfigV1(StrictBaseModel):
    enabled: StrictBool = False
    method: Literal["date_ratio", "date_cut"] = "date_ratio"
    train_ratio: float = Field(0.6, ge=0, le=1)
    valid_ratio: float = Field(0.2, ge=0, le=1)
    test_ratio: float = Field(0.2, ge=0, le=1)
    train_end: str | None = None
    valid_end: str | None = None
    min_train_days: int = Field(252, ge=1)
    min_valid_days: int = Field(126, ge=1)
    min_test_days: int = Field(126, ge=1)
    warmup_days: int | None = Field(None, ge=0)
    direction_policy: Literal["train_fixed"] = "train_fixed"

    @model_validator(mode="after")
    def validate_split(self):
        if abs((self.train_ratio + self.valid_ratio + self.test_ratio) - 1.0) > 1e-6:
            raise ValueError("validation.oos train_ratio + valid_ratio + test_ratio must equal 1.0")
        if self.method == "date_cut" and (self.train_end is None or self.valid_end is None):
            raise ValueError("validation.oos date_cut requires train_end and valid_end")
        return self


class DataQualityValidationConfigV1(StrictBaseModel):
    enabled: StrictBool = True
    mode: Literal["report_only", "filter", "strict"] = "filter"
    min_price: float = Field(0.01, gt=0)
    max_abs_daily_ret: float = Field(0.25, ge=0.05, le=1.0)
    max_missing_ratio_per_stock: float = Field(0.2, ge=0, le=1)
    require_positive_volume: bool = True
    require_positive_amount: bool = True
    drop_st: bool = False
    drop_new_listing_days: int = Field(60, ge=0)
    adjustment: Literal["qfq", "hfq", "none", "unknown"] = "unknown"
    fail_on_unknown_adjustment: bool = False


class OutputConfig(StrictBaseModel):
    report: bool = True
    signal_export: bool = False

    @field_validator("signal_export")
    @classmethod
    def reject_signal_export(cls, value: bool) -> bool:
        if value:
            raise ValueError("SignalExport is Post-MVP")
        return value


class OutputConfigV1(StrictBaseModel):
    report: bool = True
    signal_export: bool = False


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


class StrategySpecV1(StrictBaseModel):
    """Post-MVP strategy schema with versioned extensions over v0."""

    schema_version: Literal["strategy_spec/v1"]
    name: str = Field(..., min_length=1, max_length=120)
    asset_class: str = Field(..., min_length=1, max_length=40)
    market: str = Field(..., min_length=1, max_length=60)
    frequency: Literal["daily"]
    universe: str = Field(..., min_length=1, max_length=80)
    factors: list[FactorSpecV1] = Field(..., min_length=1, max_length=8)
    signal_rules: RankThresholdSignalRuleV1
    portfolio_rule: PortfolioRuleV1
    risk_rules: RiskRules
    cost_model: FixedBpsCostModel
    validation: ValidationConfigV1
    outputs: OutputConfigV1

    @model_validator(mode="before")
    @classmethod
    def reject_forbidden_execution_fields(cls, data):
        if isinstance(data, dict):
            forbidden = FORBIDDEN_EXECUTION_FIELDS & set(data)
            if forbidden:
                raise ValueError(f"Forbidden execution fields: {sorted(forbidden)}")
        return data


StrategySpec = Annotated[StrategySpecV0 | StrategySpecV1, Field(discriminator="schema_version")]


def parse_strategy_spec(data: StrategySpecV0 | StrategySpecV1 | dict) -> StrategySpecV0 | StrategySpecV1:
    if isinstance(data, (StrategySpecV0, StrategySpecV1)):
        return data
    schema_version = data.get("schema_version") if isinstance(data, dict) else None
    if schema_version == "strategy_spec/v1":
        return StrategySpecV1.model_validate(data)
    return StrategySpecV0.model_validate(data)


def _find_forbidden_keys(data: object, prefix: str = "") -> set[str]:
    found: set[str] = set()
    if isinstance(data, dict):
        for key, value in data.items():
            key_text = str(key)
            path = f"{prefix}.{key_text}" if prefix else key_text
            if key_text in FORBIDDEN_EXECUTION_FIELDS:
                found.add(path)
            found.update(_find_forbidden_keys(value, path))
    elif isinstance(data, list):
        for index, value in enumerate(data):
            found.update(_find_forbidden_keys(value, f"{prefix}.{index}" if prefix else str(index)))
    return found


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


def example_strategy_spec_v1() -> dict:
    return {
        "schema_version": "strategy_spec/v1",
        "name": "multi_factor_top_n_score_weighted",
        "asset_class": "equity",
        "market": "a_share",
        "frequency": "daily",
        "universe": "hs300",
        "factors": [
            {
                "id": "momentum_20d",
                "expression": "rank(close / ts_mean(close, 20))",
                "direction": "higher_is_better",
                "weight": 0.6,
            },
            {
                "id": "reversal_5d",
                "expression": "rank(ts_delta(close, 5))",
                "direction": "lower_is_better",
                "weight": 0.4,
            },
        ],
        "signal_rules": {
            "type": "rank_threshold",
            "top_n": 20,
        },
        "portfolio_rule": {
            "weighting": "score_weighted",
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
            "run_strategy_anti_overfit": True,
            "run_strategy_rolling_validation": True,
            "oos": {
                "enabled": False,
                "method": "date_ratio",
                "train_ratio": 0.6,
                "valid_ratio": 0.2,
                "test_ratio": 0.2,
                "direction_policy": "train_fixed",
            },
            "data_quality": {
                "enabled": False,
                "max_abs_daily_ret": 0.25,
                "max_missing_ratio_per_stock": 0.2,
                "adjustment": "unknown",
                "fail_on_unknown_adjustment": False,
            },
        },
        "outputs": {
            "report": True,
            "signal_export": True,
        },
    }
